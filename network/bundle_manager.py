"""
Relay Bundle Manager — Phase 5

Manages the relay bundle: p2pd binary, relay list, and all networking
libraries as a single independently-updatable package stored inside the
Qubes app data directory.

Bundle lives at:  {qubes_data_dir}/relay_bundle/
  relay_bundle/
    p2pd[.exe]          — Go libp2p daemon binary
    relay_list.json     — Community relay list (updated separately from app)
    bundle_version.txt  — Current bundle version string
    bundle_manifest.json — Version, checksums, update URL

The Settings → Endpoints → "Update Bundle" button calls update_bundle().
Signature verification is done against BitFaced's public key hardcoded here.

TODO Phase 5: Implement download, verify, atomic replace logic.
"""

import json
import os
import platform
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from utils.logging import get_logger

logger = get_logger(__name__)

# BitFaced's bundle signing public key (secp256k1 compressed hex).
# Hardcoded at compile time — bundles that fail this check are silently rejected.
BUNDLE_SIGNING_PUBKEY = "TODO_REPLACE_WITH_BITFACED_PUBKEY"

# Canonical bundle manifest URL (mirrored by community volunteers).
BUNDLE_MANIFEST_URL = "https://qube.cash/relay-bundle/manifest.json"


def _qubes_root_dir() -> Optional[Path]:
    """
    Detect the Qubes install root — the folder that contains Qubes.exe / Qubes
    alongside ollama/, qubes-backend/, etc.

    Search order:
      1. Parent of the running qubes-backend executable (frozen PyInstaller)
      2. Parent of the running Python executable when in dev venv
      3. None (caller falls back to project root)
    """
    if getattr(sys, "frozen", False):
        # qubes-backend.exe lives at {root}/qubes-backend/qubes-backend.exe
        # so parent.parent is the Qubes root
        candidate = Path(sys.executable).parent.parent
        if (candidate / "qubes-backend").is_dir():
            return candidate
        # Single-file build — executable IS in root
        return Path(sys.executable).parent
    return None


def get_bundle_dir(qubes_data_dir: Optional[Path] = None) -> Path:
    """
    Return the relay bundle directory.

    Resolution order (same pattern as D:\\Qubes\\ollama\\):
      1. Explicit qubes_data_dir argument
      2. D:\\Qubes\\relay\\  (or equivalent root/relay/) when running installed
      3. {project_root}/relay_bundle/ in dev mode

    Layout:
      relay/
        p2pd[.exe]           ← Go libp2p-daemon binary
        relay_list.json      ← community relay list
        bundle_version.txt
        bundle_manifest.json
    """
    if qubes_data_dir:
        bundle_dir = Path(qubes_data_dir) / "relay_bundle"
    else:
        root = _qubes_root_dir()
        if root:
            bundle_dir = root / "relay"
        else:
            # Dev mode — project root/relay_bundle
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

    # Primary: relay/ folder
    bundled = bundle_dir / binary_name
    if bundled.exists():
        return bundled

    # Secondary: _internal/ next to qubes-backend.exe
    if getattr(sys, "frozen", False):
        internal = Path(sys.executable).parent / "_internal" / binary_name
        if internal.exists():
            return internal

    # Fallback: system PATH
    import shutil
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


# TODO Phase 5: implement check_for_update(), download_bundle(), verify_signature(),
#               apply_bundle() — atomic replace + daemon restart without full app restart.
async def check_for_update(qubes_data_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Check if a newer relay bundle is available.

    TODO Phase 5: fetch BUNDLE_MANIFEST_URL, compare versions, return result.
    """
    return {
        "current_version": get_bundle_version(qubes_data_dir),
        "latest_version": "unknown",
        "update_available": False,
        "note": "Phase 5 — not yet implemented",
    }


async def update_bundle(qubes_data_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Download, verify, and apply the latest relay bundle.

    TODO Phase 5: full implementation.
    Steps:
      1. Fetch manifest from BUNDLE_MANIFEST_URL
      2. Compare version with installed
      3. Download bundle archive
      4. Verify secp256k1 signature against BUNDLE_SIGNING_PUBKEY
      5. Atomically replace relay_bundle/ directory
      6. Restart relay daemon without restarting full app
    """
    return {
        "success": False,
        "message": "Relay bundle update not yet implemented (Phase 5).",
    }
