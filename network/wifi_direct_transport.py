"""
WiFi-Direct Transport Driver — Phase 5 (Stub)

Peer-to-peer WiFi connection without an access point.
Faster than BLE (typically 10–250 Mbps vs BLE's ~1 Mbps).
Shorter range than LoRa (30–200 m vs LoRa's 1–15 km).

Platform support:
  Android:  Native WiFi-Direct API via Android Nearby (go-libp2p mobile)
  iOS:      Multipeer Connectivity Framework (abstracts over BLE + WiFi-Direct)
  Windows:  Wi-Fi Direct API (Windows.Devices.WiFiDirect namespace)
  Linux:    wpa_supplicant P2P mode (p2p_find, p2p_connect commands)
  macOS:    No public WiFi-Direct API (use BLE or AirDrop workaround)

Python library options:
  Linux:  python-wpasupplicant or direct wpa_cli subprocess calls
  Windows: No mature pure-Python library; use WinRT COM interop via comtypes

Mobile (Android/iOS):
  WiFi-Direct on mobile is handled entirely by the gomobile relay layer
  (see mobile/android/relay_node_android.py). This desktop driver is for
  Windows and Linux desktop relays only.

Addressing:
  Multiaddr format:  /wifi-direct/MAC:AA:BB:CC:DD:EE:FF/p2p/QmPeerID
  The MAC address is the WiFi interface MAC of the peer's P2P group owner.

Integration with relay:
  Priority 2 in transport waterfall (after local relay, before internet DHT).
  Falls back gracefully if WiFi-Direct is unavailable on the platform.

TODO Phase 5:
  1. Linux: subprocess wpa_cli wrapper for p2p_find + p2p_connect
  2. Windows: WinRT WiFi-Direct API via ctypes/comtypes
  3. Integrate with RelayNodeManager transport list
  4. Test interoperability between Linux and Android (most common case)
"""

import asyncio
import struct
from typing import Any, Dict, List, Optional

from utils.logging import get_logger

logger = get_logger(__name__)

CHUNK_SIZE = 60000  # WiFi-Direct can handle much larger packets than BLE
SCAN_TIMEOUT_SECS = 10.0

_WIFI_DIRECT_AVAILABLE = False  # Will be True once platform driver is implemented


class WiFiDirectTransport:
    """
    WiFi-Direct transport driver for Qubes relay (desktop: Linux + Windows).

    Implements the 4-function relay transport interface over peer-to-peer WiFi.
    Requires platform-specific WiFi P2P support.

    Mobile WiFi-Direct is handled by the Android gomobile layer, not this class.
    """

    def __init__(self) -> None:
        if not _WIFI_DIRECT_AVAILABLE:
            raise ImportError(
                "WiFi-Direct transport is not yet implemented for this platform. "
                "Use BLE transport (ble_transport.py) for offline mesh until Phase 5."
            )
        self._receive_buffers: Dict[str, bytearray] = {}
        self._receive_events: Dict[str, asyncio.Event] = {}

    async def discover_local_peers(self) -> List[str]:
        """
        Scan for nearby WiFi-Direct peers advertising the Qubes service.
        Returns list of multiaddrs: ["/wifi-direct/MAC:.../p2p/...", ...]

        TODO Phase 5: platform P2P discovery.
        """
        logger.info("wifi_direct_scan_start", timeout=SCAN_TIMEOUT_SECS)
        return []

    async def connect(self, peer_multiaddr: str) -> Any:
        """
        Initiate a WiFi-Direct P2P connection.

        Args:
            peer_multiaddr: "/wifi-direct/MAC:AA:BB:CC:DD:EE:FF/p2p/..." format.

        Returns:
            Stream handle (socket or similar) for send/receive.
        """
        parts = peer_multiaddr.split("/")
        mac = ""
        for part in parts:
            if part.startswith("MAC:"):
                mac = part[4:]
                break
        if not mac:
            raise ValueError(f"Cannot parse WiFi-Direct MAC from multiaddr: {peer_multiaddr}")

        logger.info("wifi_direct_connect", mac=mac)
        # TODO Phase 5: establish P2P group, open TCP socket over link-local addr
        stream = {"mac": mac, "transport": self}
        self._receive_buffers[mac] = bytearray()
        self._receive_events[mac] = asyncio.Event()
        return stream

    async def send(self, stream: Any, data: bytes) -> None:
        """
        Send data over a WiFi-Direct connection.
        WiFi-Direct MTU is large (jumbo frames), chunk only if needed.
        """
        mac = stream["mac"]
        fragments = [data[i:i + CHUNK_SIZE] for i in range(0, len(data), CHUNK_SIZE)]
        total = len(fragments)
        for seq, fragment in enumerate(fragments):
            header = struct.pack(">II", seq, total)
            packet = header + fragment
            # TODO Phase 5: write to TCP socket over P2P link-local address
            logger.debug("wifi_direct_send", mac=mac[:11], seq=seq, total=total)

    async def receive(self, stream: Any) -> bytes:
        """Wait for and return the next complete message."""
        mac = stream["mac"]
        event = self._receive_events.get(mac)
        if event:
            await event.wait()
            event.clear()
        data = bytes(self._receive_buffers.get(mac, bytearray()))
        self._receive_buffers[mac] = bytearray()
        return data


def is_wifi_direct_available() -> bool:
    """Return True if WiFi-Direct transport is available on this platform."""
    return _WIFI_DIRECT_AVAILABLE
