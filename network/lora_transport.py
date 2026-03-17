"""
LoRa Transport Driver — Phase 5 (Stub)

Long-range radio transport for extreme offline scenarios.
Typical bandwidth: ~250 bytes/packet. Range: 1–15 km depending on hardware.

Hardware requirements:
  - LoRa radio module (e.g. RFM95, SX1276, RAK811)
  - USB-serial adapter or GPIO connection to host
  - Antenna matched to frequency (868 MHz EU / 915 MHz US / 433 MHz Asia)

Python library options (choose one):
  pip install pyserial>=3.5      # Direct AT-command serial (most hardware)
  pip install RPi.GPIO lora      # Raspberry Pi GPIO-connected modules

Message design:
  LoRa MTU is ~255 bytes raw. After LoRaWAN overhead: ~222 bytes.
  We use the same [seq_4b][total_4b][data] framing as BLE transport.
  With 222-byte payload capacity, a 512-byte message takes 3 fragments.

  At SF7 (fastest): ~6 kbps → 3 fragments ≈ 240ms + inter-packet delay.
  At SF12 (longest range): ~290 bps → very slow; set MAX_PAYLOAD_BYTES = 51.

Addressing:
  Multiaddr format:  /lora/DevEUI:0011223344556677/p2p/QmPeerID
  DevEUI is the hardware EUI-64 of the LoRa module.

Integration with relay:
  This driver plugs into the transport fallback waterfall at Priority 5
  (lowest, after BLE). The relay protocol never changes — only the driver
  needs to implement the 4-function interface.

TODO Phase 5:
  1. Detect LoRa hardware (serial port enumeration)
  2. Implement serial AT-command driver for popular LoRa modules
  3. Implement discover_local_peers() via LoRa broadcast ping
  4. Fragment and reassemble messages within LoRa MTU constraints
  5. Add to RelayNodeManager transport list with lowest priority
"""

import asyncio
import struct
from typing import Any, Callable, Dict, List, Optional

from utils.logging import get_logger

logger = get_logger(__name__)

# LoRa payload limit (bytes) — conservative for maximum compatibility
MAX_PAYLOAD_BYTES = 200
LORA_SCAN_TIMEOUT_SECS = 30.0  # LoRa peer discovery is slow

try:
    import serial
    import serial.tools.list_ports
    _SERIAL_AVAILABLE = True
except ImportError:
    _SERIAL_AVAILABLE = False


class LoRaTransport:
    """
    LoRa transport driver for Qubes relay.

    Implements the 4-function relay transport interface over long-range radio.
    Requires serial-connected LoRa hardware and pyserial.

    Gracefully raises ImportError if pyserial not installed.
    """

    def __init__(self, serial_port: Optional[str] = None, baud_rate: int = 9600) -> None:
        if not _SERIAL_AVAILABLE:
            raise ImportError(
                "pyserial is required for LoRa transport. Install with: pip install pyserial>=3.5"
            )
        self.serial_port = serial_port  # None = auto-detect
        self.baud_rate = baud_rate
        self._conn: Optional[Any] = None  # serial.Serial instance
        self._receive_buffers: Dict[str, bytearray] = {}
        self._receive_events: Dict[str, asyncio.Event] = {}

    async def discover_local_peers(self) -> List[str]:
        """
        Broadcast a ping over LoRa and collect responses.
        Returns list of peer multiaddrs: ["/lora/DevEUI:.../p2p/...", ...]

        TODO: implement AT-command broadcast + response collection.
        """
        logger.info("lora_scan_start", timeout=LORA_SCAN_TIMEOUT_SECS)
        # TODO Phase 5: broadcast LoRa ping, collect responding DevEUIs
        return []

    async def connect(self, peer_multiaddr: str) -> Any:
        """
        'Connect' to a LoRa peer (set target DevEUI for outgoing packets).

        LoRa is connectionless — 'connect' just records the target address.

        Args:
            peer_multiaddr: "/lora/DevEUI:0011223344556677/p2p/..." format.

        Returns:
            A dict acting as the stream handle: {"deveui": "...", "transport": self}
        """
        parts = peer_multiaddr.split("/")
        deveui = ""
        for part in parts:
            if part.startswith("DevEUI:"):
                deveui = part[7:]
                break
        if not deveui:
            raise ValueError(f"Cannot parse LoRa DevEUI from multiaddr: {peer_multiaddr}")

        logger.info("lora_connect", deveui=deveui)
        # TODO Phase 5: open serial port if not already open
        stream = {"deveui": deveui, "transport": self}
        self._receive_buffers[deveui] = bytearray()
        self._receive_events[deveui] = asyncio.Event()
        return stream

    async def send(self, stream: Any, data: bytes) -> None:
        """
        Send data to a LoRa peer, fragmented to MAX_PAYLOAD_BYTES.

        Args:
            stream: Dict returned by connect() with "deveui" key.
            data: Raw bytes to send.
        """
        deveui = stream["deveui"]
        fragments = [data[i:i + MAX_PAYLOAD_BYTES] for i in range(0, len(data), MAX_PAYLOAD_BYTES)]
        total = len(fragments)
        for seq, fragment in enumerate(fragments):
            header = struct.pack(">II", seq, total)
            packet = header + fragment
            # TODO Phase 5: write packet to serial port using AT+SEND command
            logger.debug("lora_send_fragment", deveui=deveui[:12], seq=seq, total=total, bytes=len(fragment))

    async def receive(self, stream: Any) -> bytes:
        """
        Wait for and return the next complete message from a LoRa stream.

        Args:
            stream: Dict returned by connect().

        Returns:
            Reassembled message bytes.
        """
        deveui = stream["deveui"]
        event = self._receive_events.get(deveui)
        if event:
            await event.wait()
            event.clear()
        data = bytes(self._receive_buffers.get(deveui, bytearray()))
        self._receive_buffers[deveui] = bytearray()
        return data

    def _on_serial_data(self, raw_packet: bytes, sender_deveui: str) -> None:
        """
        Called by serial read loop when a LoRa packet arrives.
        Reassembles fragments and signals completion.

        TODO Phase 5: wire to serial read thread.
        """
        try:
            if len(raw_packet) < 8:
                return
            seq, total = struct.unpack_from(">II", raw_packet, 0)
            payload = raw_packet[8:]
            if sender_deveui not in self._receive_buffers:
                self._receive_buffers[sender_deveui] = bytearray()
                self._receive_events[sender_deveui] = asyncio.Event()
            self._receive_buffers[sender_deveui].extend(payload)
            if seq == total - 1:
                event = self._receive_events.get(sender_deveui)
                if event:
                    event.set()
        except Exception as exc:
            logger.debug("lora_packet_error", error=str(exc))


def is_lora_available() -> bool:
    """Return True if pyserial is installed and LoRa transport can be used."""
    return _SERIAL_AVAILABLE
