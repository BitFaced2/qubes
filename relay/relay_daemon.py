"""
Standalone Relay Daemon

For community relay operators who want to run a dedicated always-on relay node.
Every Qubes desktop user also automatically runs a lighter relay node inside
the app itself — this daemon is for dedicated community infrastructure.

Usage:
    python relay_daemon.py
    python relay_daemon.py --port 4001 --max-connections 200 --retention-days 7
    python relay_daemon.py --dev    # verbose logging, no cover traffic

Or via npm:
    npm run relay:start
    npm run relay:dev
"""

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

# Add parent directory to path so we can import from the Qubes project
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from relay.relay_config import load_config
from network.relay_list import BUILTIN_RELAYS, get_all_bootstrap_multiaddrs
from network.relay_node import RelayNodeManager


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Qubes P2P Relay Daemon — community relay node"
    )
    parser.add_argument("--port", type=int, default=None, help="Listen port (default: 4001)")
    parser.add_argument("--max-connections", type=int, default=None, help="Max connections (default: 200)")
    parser.add_argument("--retention-days", type=int, default=None, help="Store-forward retention in days (default: 7)")
    parser.add_argument("--config", type=str, default=None, help="Path to relay.json config file")
    parser.add_argument("--dev", action="store_true", help="Development mode: verbose logging")
    return parser.parse_args()


async def run_relay(config: dict) -> None:
    """Start and run the relay daemon until SIGINT/SIGTERM."""
    data_dir = Path.home() / ".qubes" / "relay_daemon"
    data_dir.mkdir(parents=True, exist_ok=True)

    relay = RelayNodeManager(
        user_data_dir=data_dir,
        listen_port=config["port"],
        max_connections=config["max_connections"],
        retention_days=config["retention_days"],
        p2pd_binary=config.get("p2pd_binary"),
        custom_peers=config.get("bootstrap_peers", []),
    )

    print(f"[Qubes Relay] Starting on port {config['port']} (max {config['max_connections']} connections)")
    print(f"[Qubes Relay] Store-and-forward retention: {config['retention_days']} days")
    print(f"[Qubes Relay] Built-in seed relays: {len(BUILTIN_RELAYS)}")
    print("[Qubes Relay] Press Ctrl+C to stop\n")

    await relay.start()

    status = relay.get_status()
    print(f"[Qubes Relay] Running — Peer ID: {status.get('peer_id', 'unknown')}")
    print(f"[Qubes Relay] Listening at: {status.get('multiaddr', 'unknown')}\n")

    # Wait for shutdown signal
    stop_event = asyncio.Event()

    def _on_signal():
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except (NotImplementedError, OSError):
            # Windows doesn't support add_signal_handler for all signals
            pass

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        pass

    print("\n[Qubes Relay] Shutting down...")
    await relay.stop()
    print("[Qubes Relay] Stopped.")


def main() -> None:
    args = parse_args()

    # Load config from file, then apply CLI overrides
    config_path = Path(args.config) if args.config else None
    config = load_config(config_path)

    if args.port is not None:
        config["port"] = args.port
    if args.max_connections is not None:
        config["max_connections"] = args.max_connections
    if args.retention_days is not None:
        config["retention_days"] = args.retention_days
    if args.dev:
        config["dev_mode"] = True
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(
            level=getattr(logging, config.get("log_level", "info").upper(), logging.INFO),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )

    asyncio.run(run_relay(config))


if __name__ == "__main__":
    main()
