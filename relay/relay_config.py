"""
Relay Daemon Configuration

Loads configuration for the standalone relay daemon from relay.json.
Community relay operators use this to configure their dedicated relay nodes.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_CONFIG: Dict[str, Any] = {
    "port": 4001,
    "max_connections": 200,
    "retention_days": 7,
    "dev_mode": False,
    "bootstrap_peers": [],          # Extra bootstrap peers beyond built-in list
    "p2pd_binary": None,            # None = auto-discover from PATH or relay_bundle/
    "log_level": "info",
    "metrics_port": 9090,           # Prometheus metrics (0 = disabled)
}

_CONFIG_FILE = "relay.json"


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Load relay daemon configuration.

    Merges relay.json overrides on top of DEFAULT_CONFIG.
    """
    config = dict(DEFAULT_CONFIG)

    if config_path is None:
        config_path = Path(__file__).parent / _CONFIG_FILE

    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                overrides = json.load(f)
            config.update(overrides)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"Warning: failed to load {config_path}: {exc}")

    return config


def save_config(config: Dict[str, Any], config_path: Optional[Path] = None) -> None:
    """Save relay daemon configuration to relay.json."""
    if config_path is None:
        config_path = Path(__file__).parent / _CONFIG_FILE

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
