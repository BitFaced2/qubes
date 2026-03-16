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


def get_bundle_dir(qubes_data_dir: Optional[Path] = None) -> Path:
    """
    Return the relay bundle directory inside the Qubes app data path.

    All relay binaries and configs live here — one clear location.
    """
    if qubes_data_dir:
        bundle_dir = Path(qubes_data_dir) / "relay_bundle"
    elif getattr(sys, "frozen", False):
        # PyInstaller bundle — place alongside the executable
        bundle_dir = Path(sys.executable).parent / "relay_bundle"
    else:
        # Dev mode — use project root
        bundle_dir = Path(__file__).resolve().parent.parent / "relay_bundle"

    bundle_dir.mkdir(parents=True, exist_ok=True)
    return bundle_dir


def get_p2pd_path(qubes_data_dir: Optional[Path] = None) -> Optional[Path]:
    """
    Return path to the bundled p2pd binary, or None if not present.

    Falls back to system PATH if not found in bundle dir.
    """
    bundle_dir = get_bundle_dir(qubes_data_dir)
    binary_name = "p2pd.exe" if platform.system() == "Windows" else "p2pd"
    bundled = bundle_dir / binary_name

    if bundled.exists():
        return bundled

    # Fall back to system PATH
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
