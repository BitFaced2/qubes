"""
I2P Transport Driver — Phase 5 (Stub)

Anonymous overlay network transport for users requiring maximum privacy.
Hides IP addresses from relay operators beyond what the onion layer provides.

I2P provides:
  - Garlic routing (multi-layer encryption, like onion routing but with bundles)
  - Persistent pseudonymous addresses (I2P destinations / .b32.i2p addresses)
  - Built-in NAT traversal (I2P handles its own hole punching)
  - Stronger anonymity than Tor for I2P-native traffic

Trade-offs vs direct TCP:
  - Higher latency (typically 1–5 seconds for unidirectional, 2–10 for bidirectional)
  - Lower throughput (shared I2P bandwidth)
  - Requires i2pd or Java I2P router running on the host

Dependencies:
  Option A (recommended): i2pd C++ router (lightweight, ~20 MB)
    Install: https://i2pd.website/en/download/
    Python bridge: pip install i2plib>=0.9.0

  Option B: Java I2P router (full-featured, heavier)
    Python bridge: SAMv3 API (I2P SAM bridge, TCP socket protocol)

  Option C: sam3 library
    pip install sam3>=0.9.0  (implements SAMv3 directly)

Addressing:
  I2P destination: base32 or base64 encoded I2P address
  Multiaddr format: /i2p/b32addr:XXXX.b32.i2p/p2p/QmPeerID

Integration with relay:
  Priority 5 in transport waterfall (lowest priority, used when user
  explicitly enables I2P mode for maximum privacy). The relay protocol
  does not change — I2P is just another transport driver.

Privacy note:
  When I2P transport is active, relay operators see only I2P destination
  addresses (which are unlinkable to IP addresses), providing an additional
  privacy layer on top of the onion routing already in Phase 2.

TODO Phase 5:
  1. Detect i2pd or Java I2P router (check localhost:7657 for router console)
  2. Implement SAMv3 session creation via sam3 or i2plib
  3. implement discover_local_peers() via I2P netDB (bootstrap peers only)
  4. Wire into RelayNodeManager as lowest-priority optional transport
  5. UI toggle in Settings → Relay → "Use I2P for maximum privacy"
"""

import asyncio
import struct
from typing import Any, Dict, List, Optional

from utils.logging import get_logger

logger = get_logger(__name__)

CHUNK_SIZE = 32000   # I2P max datagram size
SCAN_TIMEOUT_SECS = 60.0  # I2P discovery is slow (netDB lookup)

# SAMv3 bridge defaults (i2pd / Java I2P router)
SAM_HOST = "127.0.0.1"
SAM_PORT = 7656

try:
    import sam3
    _SAM3_AVAILABLE = True
except ImportError:
    try:
        import i2plib
        _I2PLIB_AVAILABLE = True
        _SAM3_AVAILABLE = False
    except ImportError:
        _SAM3_AVAILABLE = False
        _I2PLIB_AVAILABLE = False

_I2P_AVAILABLE = _SAM3_AVAILABLE or locals().get("_I2PLIB_AVAILABLE", False)


class I2PTransport:
    """
    I2P transport driver for Qubes relay — maximum privacy mode.

    Implements the 4-function relay transport interface over I2P.
    Requires i2pd or Java I2P router + sam3 or i2plib Python bindings.

    Only used when explicitly enabled by the user via Settings → Relay.
    Falls back gracefully if I2P router is not running.
    """

    def __init__(self) -> None:
        if not _I2P_AVAILABLE:
            raise ImportError(
                "I2P transport requires an I2P router and Python bindings. "
                "Install i2pd from https://i2pd.website and run: pip install sam3>=0.9.0"
            )
        self._session: Optional[Any] = None  # SAM3 session
        self._destination: Optional[str] = None  # Our I2P destination
        self._receive_buffers: Dict[str, bytearray] = {}
        self._receive_events: Dict[str, asyncio.Event] = {}

    async def start(self) -> None:
        """
        Create a SAMv3 session with the local I2P router.
        Generates an I2P destination (persistent pseudonymous address).

        TODO Phase 5: implement SAMv3 session creation.
        """
        logger.info("i2p_session_start", sam_host=SAM_HOST, sam_port=SAM_PORT)
        # TODO Phase 5: await sam3.SAMSession.create(SAM_HOST, SAM_PORT)
        # self._destination = session.destination

    async def stop(self) -> None:
        """Close the SAMv3 session."""
        if self._session:
            # TODO Phase 5: await self._session.close()
            self._session = None
        logger.info("i2p_session_stopped")

    @property
    def destination(self) -> Optional[str]:
        """Our I2P destination address (.b32.i2p format)."""
        return self._destination

    async def discover_local_peers(self) -> List[str]:
        """
        Look up known Qubes relay I2P destinations from the I2P netDB.
        Returns list of multiaddrs: ["/i2p/b32addr:.../p2p/...", ...]

        I2P has no mDNS equivalent — peers must be known in advance via
        DHT or out-of-band exchange (normal relay list bootstrapping covers this).

        TODO Phase 5: resolve known relay I2P destinations from relay_list.json.
        """
        logger.info("i2p_discover", note="using relay list, not netDB scan")
        return []

    async def connect(self, peer_multiaddr: str) -> Any:
        """
        Open an I2P streaming connection to a peer.

        Args:
            peer_multiaddr: "/i2p/b32addr:XXX.b32.i2p/p2p/..." format.

        Returns:
            Stream handle for send/receive.
        """
        parts = peer_multiaddr.split("/")
        b32addr = ""
        for part in parts:
            if part.startswith("b32addr:"):
                b32addr = part[8:]
                break
        if not b32addr:
            raise ValueError(f"Cannot parse I2P destination from multiaddr: {peer_multiaddr}")

        logger.info("i2p_connect", dest=b32addr[:20])
        # TODO Phase 5: await session.connect(b32addr)
        stream = {"dest": b32addr, "transport": self}
        self._receive_buffers[b32addr] = bytearray()
        self._receive_events[b32addr] = asyncio.Event()
        return stream

    async def send(self, stream: Any, data: bytes) -> None:
        """Send data over an I2P streaming connection."""
        dest = stream["dest"]
        fragments = [data[i:i + CHUNK_SIZE] for i in range(0, len(data), CHUNK_SIZE)]
        total = len(fragments)
        for seq, fragment in enumerate(fragments):
            header = struct.pack(">II", seq, total)
            packet = header + fragment
            # TODO Phase 5: write to SAM3 stream socket
            logger.debug("i2p_send", dest=dest[:12], seq=seq, total=total)

    async def receive(self, stream: Any) -> bytes:
        """Wait for and return the next complete message from an I2P stream."""
        dest = stream["dest"]
        event = self._receive_events.get(dest)
        if event:
            await event.wait()
            event.clear()
        data = bytes(self._receive_buffers.get(dest, bytearray()))
        self._receive_buffers[dest] = bytearray()
        return data


def is_i2p_available() -> bool:
    """Return True if I2P transport can be used on this system."""
    return _I2P_AVAILABLE
