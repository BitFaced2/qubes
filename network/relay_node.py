"""
Relay Node Manager — Phase 1

High-level manager for the Qubes P2P relay node. Wraps QubeP2PNode and
LibP2PDaemonBridge, loads the built-in relay list, manages custom peers,
and exposes a clean interface to gui_bridge.py.

Transport fallback waterfall (Priority 1 → 5):
  1. Direct local relay (this node)
  2. Internet DHT routing (Kademlia via p2pd)
  3. Nostr relay fallback (Phase 3 — see nostr_transport.py)
  4. BLE / LoRa mesh (Phase 6)
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from network.p2p_node import QubeP2PNode
from network.libp2p_daemon_bridge import LibP2PDaemonBridge
from network.relay_list import BUILTIN_RELAYS, get_all_bootstrap_multiaddrs
from network.store_forward import StoreForwardQueue
from network.onion import OnionRouter
from network.cover_traffic import CoverTrafficManager
from network.nostr_transport import NostrTransport
from network.bundle_manager import ensure_p2pd_binary
from utils.logging import get_logger

logger = get_logger(__name__)


class RelayNodeManager:
    """
    High-level P2P relay node for Qubes.

    Manages:
    - Local p2pd daemon lifecycle (start / stop)
    - Built-in + user-configured seed peers
    - Outbound message sending via DHT routing
    - Inbound store-and-forward message delivery
    - Peer reachability polling (for UI status dots)
    """

    def __init__(
        self,
        user_data_dir: Path,
        listen_port: int = 0,
        max_connections: int = 50,
        retention_days: int = 7,
        p2pd_binary: Optional[str] = None,
        custom_peers: Optional[List[str]] = None,
        nostr_relay_urls: Optional[List[str]] = None,
    ):
        self.user_data_dir = Path(user_data_dir)
        self.listen_port = listen_port
        self.max_connections = max_connections
        self.retention_days = retention_days
        self.p2pd_binary = p2pd_binary  # None = use bundled binary

        # Built-in + user custom peers
        bootstrap = get_all_bootstrap_multiaddrs()
        if custom_peers:
            bootstrap.extend(custom_peers)
        self.bootstrap_peers = bootstrap
        self.custom_peers: List[str] = list(custom_peers or [])

        # p2pd bridge (initialised in start())
        self._bridge: Optional[LibP2PDaemonBridge] = None
        self._p2p_node: Optional[QubeP2PNode] = None

        # Store-and-forward queue
        queue_dir = self.user_data_dir / "relay_queue"
        queue_dir.mkdir(parents=True, exist_ok=True)
        self.store_forward = StoreForwardQueue(queue_dir, retention_days=retention_days)

        # Runtime state
        self.is_running = False
        self.peer_id: Optional[str] = None
        self.multiaddr: Optional[str] = None

        # Peer reachability cache: multiaddr → {online, latency_ms, last_checked}
        self._peer_status: Dict[str, Dict[str, Any]] = {}
        self._poll_task: Optional[asyncio.Task] = None

        # Phase 2: onion routing + cover traffic
        self._onion = OnionRouter()
        self._cover = CoverTrafficManager()

        # Phase 2: peer pubkey cache — populated as peers connect: peer_id → pubkey_bytes
        self._peer_pubkeys: Dict[str, bytes] = {}

        # Phase 3: Nostr fallback transport — relay_urls from EndpointPreferences
        _nostr_urls = list(nostr_relay_urls) if nostr_relay_urls else []
        self._nostr: Optional[NostrTransport] = NostrTransport(_nostr_urls) if _nostr_urls else None
        self._nostr_privkey_int: Optional[int] = None  # ephemeral privkey; set in start()

        logger.info("relay_node_manager_initialized", listen_port=listen_port)

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def start(self) -> None:
        """Start the local relay daemon and connect to bootstrap peers."""
        if self.is_running:
            return

        try:
            # Auto-download p2pd binary if not present
            if not self.p2pd_binary:
                downloaded = await ensure_p2pd_binary()
                if downloaded:
                    self.p2pd_binary = str(downloaded)

            self._bridge = LibP2PDaemonBridge(
                qube_id="relay",
                listen_port=self.listen_port,
                bootstrap_peers=self.bootstrap_peers,
                daemon_binary=self.p2pd_binary or "p2pd",
            )
            await self._bridge.start()

            self.peer_id = getattr(self._bridge, "peer_id", None)
            self.multiaddr = getattr(self._bridge, "multiaddr", None)
            self.is_running = True

            # Purge expired messages from the queue on startup
            self.store_forward.purge_expired()

            # Start background peer polling (every 30 s)
            self._poll_task = asyncio.create_task(self._poll_peers_loop())

            # Phase 2: start cover traffic
            await self._cover.start(self)

            # Phase 3: generate ephemeral Nostr privkey for this session
            import os as _os
            _raw = _os.urandom(32)
            _N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
            self._nostr_privkey_int = int.from_bytes(_raw, "big") % (_N - 1) + 1

            # Start Nostr transport if relay URLs were configured
            if self._nostr:
                await self._nostr.start()

            logger.info("relay_node_started", peer_id=self.peer_id, multiaddr=self.multiaddr)

        except Exception as exc:
            self.is_running = False
            logger.warning("relay_node_start_failed", error=str(exc))
            raise

    async def stop(self) -> None:
        """Stop the relay daemon, cover traffic, and Nostr transport."""
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

        # Phase 2: stop cover traffic
        await self._cover.stop()

        # Phase 3: stop Nostr transport
        if self._nostr:
            await self._nostr.stop()

        if self._bridge:
            try:
                await self._bridge.stop()
            except Exception:
                pass
            self._bridge = None

        self.is_running = False
        self.peer_id = None
        self.multiaddr = None
        logger.info("relay_node_stopped")

    # -------------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Return current relay node status for the Settings UI."""
        return {
            "running": self.is_running,
            "peer_id": self.peer_id,
            "multiaddr": self.multiaddr,
            "peer_count": len(self._peer_status),
            "online_peers": sum(1 for p in self._peer_status.values() if p.get("online")),
        }

    # -------------------------------------------------------------------------
    # Peer management
    # -------------------------------------------------------------------------

    def get_peers(self) -> List[Dict[str, Any]]:
        """
        Return combined peer list:
        - Built-in seed relays (read-only, with live status)
        - User custom peers (removable, with live status)
        """
        peers = []

        for relay in BUILTIN_RELAYS:
            for addr in relay["multiaddrs"]:
                status = self._peer_status.get(addr, {})
                peers.append({
                    "multiaddr": addr,
                    "label": relay["label"],
                    "operator": relay["operator"],
                    "builtin": True,
                    "online": status.get("online", None),   # None = not yet checked
                    "latency_ms": status.get("latency_ms", None),
                })

        for addr in self.custom_peers:
            status = self._peer_status.get(addr, {})
            peers.append({
                "multiaddr": addr,
                "label": addr,
                "operator": "custom",
                "builtin": False,
                "online": status.get("online", None),
                "latency_ms": status.get("latency_ms", None),
            })

        return peers

    async def add_peer(self, multiaddr: str) -> bool:
        """Add a custom relay peer."""
        if multiaddr in self.custom_peers:
            return False
        self.custom_peers.append(multiaddr)
        if self._bridge:
            try:
                await self._bridge.connect(multiaddr)
            except Exception:
                pass
        logger.info("relay_peer_added", multiaddr=multiaddr)
        return True

    async def remove_peer(self, multiaddr: str) -> bool:
        """Remove a custom relay peer."""
        if multiaddr not in self.custom_peers:
            return False
        self.custom_peers.remove(multiaddr)
        logger.info("relay_peer_removed", multiaddr=multiaddr)
        return True

    def register_peer_pubkey(self, peer_id: str, pubkey_bytes: bytes) -> None:
        """
        Register a peer's secp256k1 pubkey for Phase 2 onion routing.
        Called by the bridge when peers exchange keys during handshake.
        """
        self._peer_pubkeys[peer_id] = pubkey_bytes

    def update_nostr_relays(self, relay_urls: List[str]) -> None:
        """
        Update Nostr relay URLs (called when EndpointPreferences change).
        Takes effect on the next relay start.
        """
        if self._nostr:
            self._nostr.relay_urls = list(relay_urls)
        else:
            self._nostr = NostrTransport(list(relay_urls))

    # -------------------------------------------------------------------------
    # Messaging
    # -------------------------------------------------------------------------

    async def send_message(
        self,
        recipient_qube_id: str,
        encrypted_payload: bytes,
        ttl_days: int = 7,
        recipient_pubkey_hex: Optional[str] = None,
    ) -> bool:
        """
        Send an encrypted message to a recipient Qube.

        Transport fallback waterfall:
          1. DHT routing via live p2pd bridge  (with Phase 2 onion wrap)
          2. Nostr relay fallback              (Phase 3, requires recipient_pubkey_hex)
          3. Store-and-forward queue           (held until recipient comes online)

        Args:
            recipient_qube_id: 8-char Qube token prefix.
            encrypted_payload: Already ECIES-encrypted bytes.
            ttl_days: Store-and-forward retention if all transports fail.
            recipient_pubkey_hex: secp256k1 pubkey hex for Nostr fallback addressing.
        """
        if self._bridge and self.is_running:
            try:
                # Phase 2: wrap in 2-hop onion if 2+ peers with known pubkeys are available
                payload_to_send = encrypted_payload
                online_peers_with_keys = [
                    (pid, pk)
                    for pid, pk in self._peer_pubkeys.items()
                    if self._peer_status.get(pid, {}).get("online")
                ]
                if len(online_peers_with_keys) >= 2:
                    r1_id, r1_pk = online_peers_with_keys[0]
                    r2_id, r2_pk = online_peers_with_keys[1]
                    r1_addr = next(
                        (p["multiaddr"] for p in self.get_peers() if p.get("online") and r1_id in p["multiaddr"]),
                        r1_id,
                    )
                    r2_addr = next(
                        (p["multiaddr"] for p in self.get_peers() if p.get("online") and r2_id in p["multiaddr"]),
                        r2_id,
                    )
                    payload_to_send = await self._onion.wrap_message(
                        encrypted_payload,
                        r1_pk,
                        r2_pk,
                        b"",           # dest pubkey unknown at this layer; inner already ECIES-encrypted
                        f"qube/{recipient_qube_id}/inbox",
                        r2_addr,
                    )

                topic = f"qube/{recipient_qube_id}/inbox"
                await self._bridge.publish(topic, payload_to_send)
                logger.info("relay_message_sent", recipient=recipient_qube_id, bytes=len(payload_to_send))
                return True
            except Exception as exc:
                logger.warning("relay_direct_send_failed", recipient=recipient_qube_id, error=str(exc))

                # Phase 3: Nostr fallback if DHT unreachable and recipient pubkey is known
                if (
                    self._nostr
                    and self._nostr._running
                    and self._nostr_privkey_int
                    and recipient_pubkey_hex
                ):
                    try:
                        nostr_ok = await self._nostr.send(
                            recipient_pubkey_hex,
                            encrypted_payload,
                            our_privkey_int=self._nostr_privkey_int,
                        )
                        if nostr_ok:
                            logger.info("relay_nostr_fallback_sent", recipient=recipient_qube_id)
                            return True
                    except Exception as nostr_exc:
                        logger.warning("relay_nostr_fallback_failed", error=str(nostr_exc))

        # Fall back to store-and-forward
        import time
        ttl_ts = int(time.time()) + ttl_days * 86400
        self.store_forward.enqueue(recipient_qube_id, encrypted_payload, ttl_ts)
        logger.info("relay_message_queued", recipient=recipient_qube_id)
        return True

    async def get_pending_messages(self, qube_id: str) -> List[Dict[str, Any]]:
        """Return and drain store-and-forward messages for a Qube."""
        messages = self.store_forward.dequeue(qube_id)
        return [{"payload": m["payload"], "queued_at": m["queued_at"]} for m in messages]

    # -------------------------------------------------------------------------
    # Background peer polling
    # -------------------------------------------------------------------------

    async def _poll_peers_loop(self) -> None:
        """Poll all known peers for reachability every 30 s."""
        while self.is_running:
            await self._check_all_peers()
            await asyncio.sleep(30)

    async def _check_all_peers(self) -> None:
        """Ping all peers and update status cache."""
        all_addrs: List[str] = []
        for relay in BUILTIN_RELAYS:
            all_addrs.extend(relay["multiaddrs"])
        all_addrs.extend(self.custom_peers)

        for addr in all_addrs:
            start = time.monotonic()
            online = False
            latency_ms = None
            try:
                if self._bridge:
                    await asyncio.wait_for(self._bridge.ping(addr), timeout=5.0)
                    latency_ms = int((time.monotonic() - start) * 1000)
                    online = True
            except Exception:
                pass
            self._peer_status[addr] = {
                "online": online,
                "latency_ms": latency_ms,
                "last_checked": int(time.time()),
            }
