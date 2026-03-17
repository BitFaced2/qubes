"""
Relay Bundle Manager — Phase 5

Manages the relay bundle: p2pd binary, relay list, and all networking
libraries as a single independently-updatable package stored inside the
Qubes app data directory.

Bundle lives at:  {qubes_data_dir}/relay_bundle/  (or D:\\Qubes\\relay\\ when installed)
  relay/
    p2pd[.exe]           — Go libp2p daemon binary
    relay_list.json      — Community relay list (updated separately from app)
    bundle_version.txt   — Current bundle version string
    bundle_manifest.json — Version, checksums, update URL

The Settings → Endpoints → "Update Bundle" button calls update_bundle().
Signature verification is done against BitFaced's public key hardcoded here.
"""

import asyncio
import hashlib
import json
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

from utils.logging import get_logger

logger = get_logger(__name__)

# BitFaced's bundle signing public key (secp256k1 compressed hex).
# Hardcoded at compile time — bundles that fail this check are silently rejected.
BUNDLE_SIGNING_PUBKEY = "TODO_REPLACE_WITH_BITFACED_PUBKEY"

# Canonical bundle manifest URL (mirrored by community volunteers).
BUNDLE_MANIFEST_URL = "https://qube.cash/relay-bundle/manifest.json"

# Download timeout in seconds
DOWNLOAD_TIMEOUT = 120


def _qubes_root_dir() -> Optional[Path]:
    """
    Detect the Qubes install root — the folder that contains Qubes.exe / Qubes
    alongside ollama/, qubes-backend/, etc.

    Search order:
      1. Parent of the running qubes-backend executable (frozen PyInstaller)
      2. None (caller falls back to project root)
    """
    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).parent.parent
        if (candidate / "qubes-backend").is_dir():
            return candidate
        return Path(sys.executable).parent
    return None


def get_bundle_dir(qubes_data_dir: Optional[Path] = None) -> Path:
    """
    Return the relay bundle directory.

    Resolution order (same pattern as D:\\Qubes\\ollama\\):
      1. Explicit qubes_data_dir argument
      2. D:\\Qubes\\relay\\  (or equivalent root/relay/) when running installed
      3. {project_root}/relay_bundle/ in dev mode
    """
    if qubes_data_dir:
        bundle_dir = Path(qubes_data_dir) / "relay_bundle"
    else:
        root = _qubes_root_dir()
        if root:
            bundle_dir = root / "relay"
        else:
            bundle_dir = Path(__file__).resolve().parent.parent / "relay_bundle"

    bundle_dir.mkdir(parents=True, exist_ok=True)
    return bundle_dir


def get_p2pd_path(qubes_data_dir: Optional[Path] = None) -> Optional[Path]:
    """
    Return path to the p2pd binary, or None if not present.

    Search order:
      1. relay/ folder (D:\\Qubes\\relay\\p2pd.exe)
      2. qubes-backend\\_internal\\  (bundled alongside Python sidecar)
      3. System PATH
    """
    bundle_dir = get_bundle_dir(qubes_data_dir)
    binary_name = "p2pd.exe" if platform.system() == "Windows" else "p2pd"

    bundled = bundle_dir / binary_name
    if bundled.exists():
        return bundled

    if getattr(sys, "frozen", False):
        internal = Path(sys.executable).parent / "_internal" / binary_name
        if internal.exists():
            return internal

    system_p2pd = shutil.which("p2pd")
    if system_p2pd:
        return Path(system_p2pd)

    return None


def get_bundle_version(qubes_data_dir: Optional[Path] = None) -> str:
    """Return the installed bundle version, or 'none' if not installed."""
    version_file = get_bundle_dir(qubes_data_dir) / "bundle_version.txt"
    if version_file.exists():
        return version_file.read_text(encoding="utf-8").strip()
    return "none"


def get_bundle_manifest(qubes_data_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Return the installed bundle manifest."""
    manifest_file = get_bundle_dir(qubes_data_dir) / "bundle_manifest.json"
    if manifest_file.exists():
        try:
            return json.loads(manifest_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

def _verify_bundle_signature(file_sha256_hex: str, signature_hex: str) -> bool:
    """
    Verify secp256k1 ECDSA signature of the bundle SHA-256 hash.

    Returns True if verification passes OR if BUNDLE_SIGNING_PUBKEY is the
    placeholder (allows dev/testing without a real key).
    """
    if "TODO" in BUNDLE_SIGNING_PUBKEY:
        logger.warning("bundle_sig_skip_todo_pubkey")
        return True  # Dev mode: skip verification

    try:
        from ecdsa import VerifyingKey, SECP256k1, BadSignatureError

        vk = VerifyingKey.from_string(
            bytes.fromhex(BUNDLE_SIGNING_PUBKEY), curve=SECP256k1
        )
        sig_bytes = bytes.fromhex(signature_hex)
        data_bytes = bytes.fromhex(file_sha256_hex)
        vk.verify_digest(sig_bytes, data_bytes)
        return True
    except Exception as exc:
        logger.warning("bundle_sig_invalid", error=str(exc))
        return False


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Update logic
# ---------------------------------------------------------------------------

async def _fetch_manifest() -> Optional[Dict[str, Any]]:
    """Fetch the bundle manifest JSON from BUNDLE_MANIFEST_URL."""
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(
                BUNDLE_MANIFEST_URL,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                text = await resp.text()
                return json.loads(text)
    except Exception as exc:
        logger.warning("bundle_manifest_fetch_failed", url=BUNDLE_MANIFEST_URL, error=str(exc))
        return None


async def _download_file(url: str, dest: Path) -> bool:
    """Stream-download url to dest file. Returns True on success."""
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                with open(dest, "wb") as f:
                    async for chunk in resp.content.iter_chunked(65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = downloaded * 100 // total
                            logger.debug("bundle_download_progress", pct=pct)
        logger.info("bundle_download_done", dest=str(dest), bytes=downloaded)
        return True
    except Exception as exc:
        logger.warning("bundle_download_failed", url=url[:60], error=str(exc))
        return False


def _extract_bundle(archive_path: Path, dest_dir: Path) -> bool:
    """Extract .zip or .tar.gz archive to dest_dir. Returns True on success."""
    try:
        name = archive_path.name.lower()
        if name.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as z:
                z.extractall(dest_dir)
        elif name.endswith((".tar.gz", ".tgz")):
            with tarfile.open(archive_path, "r:gz") as t:
                t.extractall(dest_dir)
        else:
            logger.warning("bundle_unknown_format", name=archive_path.name)
            return False

        # Make p2pd executable on non-Windows
        if platform.system() != "Windows":
            p2pd = dest_dir / "p2pd"
            if p2pd.exists():
                p2pd.chmod(p2pd.stat().st_mode | 0o111)

        return True
    except Exception as exc:
        logger.warning("bundle_extract_failed", error=str(exc))
        return False


async def check_for_update(qubes_data_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Check if a newer relay bundle is available.

    Returns dict with:
      current_version, latest_version, update_available, manifest (if fetched)
    """
    current = get_bundle_version(qubes_data_dir)
    manifest = await _fetch_manifest()

    if not manifest:
        return {
            "current_version": current,
            "latest_version": "unknown",
            "update_available": False,
            "error": "Could not fetch manifest",
        }

    latest = manifest.get("version", "unknown")

    def _version_tuple(v: str):
        try:
            return tuple(int(x) for x in v.split("."))
        except Exception:
            return (0,)

    update_available = (
        latest != "unknown"
        and current != "none"
        and _version_tuple(latest) > _version_tuple(current)
    ) or (current == "none" and latest != "unknown")

    return {
        "current_version": current,
        "latest_version": latest,
        "update_available": update_available,
        "manifest": manifest,
    }


async def update_bundle(qubes_data_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Download, verify, and atomically apply the latest relay bundle.

    Steps:
      1. Fetch manifest from BUNDLE_MANIFEST_URL
      2. Compare version with installed
      3. Download bundle archive to temp file
      4. Verify SHA-256 checksum
      5. Verify secp256k1 signature against BUNDLE_SIGNING_PUBKEY
      6. Atomically replace relay/ directory
      7. Write bundle_version.txt and bundle_manifest.json
    """
    bundle_dir = get_bundle_dir(qubes_data_dir)

    # Step 1-2: check if update is needed
    check = await check_for_update(qubes_data_dir)
    if not check.get("update_available"):
        return {
            "success": False,
            "message": f"Already up to date (version {check.get('current_version')}).",
            "current_version": check.get("current_version"),
        }

    manifest = check["manifest"]
    download_url = manifest.get("download_url")
    expected_sha256 = manifest.get("sha256", "")
    signature_hex = manifest.get("signature", "")
    new_version = manifest.get("version", "unknown")

    if not download_url:
        return {"success": False, "message": "Manifest missing download_url."}

    logger.info("bundle_update_start", version=new_version, url=download_url[:60])

    # Step 3: download to temp file
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        archive_suffix = ".zip" if download_url.lower().endswith(".zip") else ".tar.gz"
        archive_path = tmp / f"relay_bundle{archive_suffix}"

        if not await _download_file(download_url, archive_path):
            return {"success": False, "message": "Download failed."}

        # Step 4: verify SHA-256
        if expected_sha256:
            actual_sha256 = _sha256_file(archive_path)
            if actual_sha256 != expected_sha256:
                logger.warning(
                    "bundle_sha256_mismatch",
                    expected=expected_sha256,
                    actual=actual_sha256,
                )
                return {"success": False, "message": "SHA-256 checksum mismatch — bundle corrupt."}

        # Step 5: verify signature
        if signature_hex and expected_sha256:
            if not _verify_bundle_signature(expected_sha256, signature_hex):
                return {"success": False, "message": "Signature verification failed — bundle rejected."}

        # Step 6: extract to temp dir then atomically swap
        extract_dir = tmp / "extracted"
        extract_dir.mkdir()
        if not _extract_bundle(archive_path, extract_dir):
            return {"success": False, "message": "Archive extraction failed."}

        # Atomic replace: backup existing → move new in → delete backup
        backup_dir = bundle_dir.parent / (bundle_dir.name + ".backup")
        try:
            if bundle_dir.exists():
                if backup_dir.exists():
                    shutil.rmtree(backup_dir)
                shutil.move(str(bundle_dir), str(backup_dir))

            shutil.copytree(str(extract_dir), str(bundle_dir))

            # Step 7: write metadata
            (bundle_dir / "bundle_version.txt").write_text(new_version, encoding="utf-8")
            (bundle_dir / "bundle_manifest.json").write_text(
                json.dumps(manifest, indent=2), encoding="utf-8"
            )

            # Remove backup on success
            if backup_dir.exists():
                shutil.rmtree(backup_dir)

        except Exception as exc:
            # Restore backup if swap failed
            if backup_dir.exists() and not bundle_dir.exists():
                shutil.move(str(backup_dir), str(bundle_dir))
            logger.warning("bundle_swap_failed", error=str(exc))
            return {"success": False, "message": f"Atomic replace failed: {exc}"}

    logger.info("bundle_update_done", version=new_version)
    return {
        "success": True,
        "version": new_version,
        "message": f"Bundle updated to {new_version}. Restart relay to apply.",
    }


# ---------------------------------------------------------------------------
# p2pd auto-download on first run
# ---------------------------------------------------------------------------

# go-libp2p-daemon release asset naming pattern
_P2PD_VERSION = "0.3.1"  # Minimum supported version
_P2PD_BASE_URL = "https://github.com/libp2p/go-libp2p-daemon/releases/download"

_P2PD_ASSETS: Dict[str, str] = {
    "Windows-x86_64":  f"{_P2PD_BASE_URL}/v{_P2PD_VERSION}/p2pd_windows_amd64.exe",
    "Windows-AMD64":   f"{_P2PD_BASE_URL}/v{_P2PD_VERSION}/p2pd_windows_amd64.exe",
    "Darwin-x86_64":   f"{_P2PD_BASE_URL}/v{_P2PD_VERSION}/p2pd_darwin_amd64",
    "Darwin-arm64":    f"{_P2PD_BASE_URL}/v{_P2PD_VERSION}/p2pd_darwin_arm64",
    "Linux-x86_64":    f"{_P2PD_BASE_URL}/v{_P2PD_VERSION}/p2pd_linux_amd64",
    "Linux-aarch64":   f"{_P2PD_BASE_URL}/v{_P2PD_VERSION}/p2pd_linux_arm64",
}


def _p2pd_download_url() -> Optional[str]:
    """Return the download URL for p2pd matching the current OS/arch, or None."""
    key = f"{platform.system()}-{platform.machine()}"
    return _P2PD_ASSETS.get(key)


async def ensure_p2pd_binary(qubes_data_dir: Optional[Path] = None) -> Optional[Path]:
    """
    Ensure the p2pd binary is present. Downloads it automatically if missing.

    Called by RelayNodeManager.start() before spawning the daemon.
    Returns the path to the binary, or None if download failed.

    The binary is placed in get_bundle_dir() / p2pd[.exe].
    """
    existing = get_p2pd_path(qubes_data_dir)
    if existing:
        logger.debug("p2pd_already_present", path=str(existing))
        return existing

    url = _p2pd_download_url()
    if not url:
        logger.warning(
            "p2pd_no_download_url",
            system=platform.system(),
            machine=platform.machine(),
            hint="Download p2pd manually from https://github.com/libp2p/go-libp2p-daemon/releases",
        )
        return None

    bundle_dir = get_bundle_dir(qubes_data_dir)
    binary_name = "p2pd.exe" if platform.system() == "Windows" else "p2pd"
    dest = bundle_dir / binary_name

    logger.info("p2pd_auto_download_start", url=url, dest=str(dest))

    try:
        success = await _download_file(url, dest)
        if not success:
            return None

        # Make executable on Unix
        if platform.system() != "Windows":
            import stat
            dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        logger.info("p2pd_auto_download_done", path=str(dest))
        return dest

    except Exception as exc:
        logger.warning("p2pd_auto_download_failed", error=str(exc))
        if dest.exists():
            dest.unlink()
        return None
