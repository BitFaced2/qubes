"""
BLE Transport Driver — Phase 6

Bluetooth Low Energy transport for offline mesh communication between
nearby Qubes devices (no internet required).

Implements the 4-function relay transport interface:
  connect(peer_multiaddr) → stream
  send(stream, bytes) → None
  receive(stream) → bytes
  discover_local_peers() → List[str]

Also implements BLE GATT server (peripheral/advertise role) via bless:
  BLEServer — advertises the Qubes relay service so other devices can
  discover and connect to this node without internet (BitChat-parity).

Requires:
  pip install bleak>=0.21.0   # Central (client/scan) role
  pip install bless>=0.2.5    # Peripheral (server/advertise) role

If bleak/bless are not installed, the respective class raises ImportError
on instantiation and the relay falls back gracefully.

BLE GATT service:
  Service UUID:      QUBES_BLE_SERVICE_UUID
  Write char UUID:   QUBES_BLE_WRITE_CHAR   (client → server)
  Notify char UUID:  QUBES_BLE_NOTIFY_CHAR  (server → client)

Message framing:
  Messages are fragmented into CHUNK_SIZE (488) byte pieces.
  Each fragment: [seq_4b][total_4b][data]
  Final reassembly at receiver.

Platform notes:
  Windows: WinRT BLE stack (bleak) + WinRT GattServiceProvider (bless)
  macOS:   CoreBluetooth via bleak + bless
  Linux:   BlueZ via D-Bus (requires bluetoothd running)
  Android: Handled by the mobile layer (not this file)
  iOS:     CoreBluetooth (handled by mobile layer)
"""

import asyncio
import struct
from typing import Any, Callable, Dict, List, Optional

from utils.logging import get_logger

logger = get_logger(__name__)

# Fixed UUIDs for Qubes relay BLE service
QUBES_BLE_SERVICE_UUID = "12a24d53-dcc0-75dd-7226-ea319f20d43d"
QUBES_BLE_WRITE_CHAR   = "12a24d54-dcc0-75dd-7226-ea319f20d43d"
QUBES_BLE_NOTIFY_CHAR  = "12a24d55-dcc0-75dd-7226-ea319f20d43d"

# BLE MTU constraint — fragment size in bytes (leave room for framing)
CHUNK_SIZE = 488  # 512 - 24 byte framing overhead

# Discovery scan duration
SCAN_TIMEOUT_SECS = 5.0

try:
    from bleak import BleakScanner, BleakClient
    _BLEAK_AVAILABLE = True
except ImportError:
    _BLEAK_AVAILABLE = False

try:
    from bless import BlessServer, BlessGATTCharacteristic, GATTCharacteristicProperties, GATTAttributePermissions
    _BLESS_AVAILABLE = True
except ImportError:
    _BLESS_AVAILABLE = False


class BLETransport:
    """
    BLE transport driver for Qubes P2P relay.

    Usage:
        transport = BLETransport()
        peers = await transport.discover_local_peers()
        stream = await transport.connect(peers[0])
        await transport.send(stream, b"hello")
        data = await transport.receive(stream)
        await stream.disconnect()

    The stream object is a BleakClient instance.
    """

    def __init__(self) -> None:
        if not _BLEAK_AVAILABLE:
            raise ImportError(
                "bleak is required for BLE transport. Install with: pip install bleak>=0.21.0"
            )
        self._receive_buffers: Dict[str, bytearray] = {}
        self._receive_events: Dict[str, asyncio.Event] = {}

    async def discover_local_peers(self) -> List[str]:
        """
        Scan for nearby BLE devices advertising the Qubes relay service.

        Returns:
            List of multiaddr strings: ["/ble/MAC:AA:BB:CC:DD:EE:FF/p2p/...", ...]
        """
        logger.info("ble_scan_start", timeout=SCAN_TIMEOUT_SECS)
        try:
            devices = await BleakScanner.discover(
                timeout=SCAN_TIMEOUT_SECS,
                service_uuids=[QUBES_BLE_SERVICE_UUID],
            )
            peers = [f"/ble/MAC:{d.address}/p2p/{d.name or 'unknown'}" for d in devices]
            logger.info("ble_scan_done", peers_found=len(peers))
            return peers
        except Exception as exc:
            logger.warning("ble_scan_failed", error=str(exc))
            return []

    async def connect(self, peer_multiaddr: str) -> Any:
        """
        Connect to a BLE peer.

        Args:
            peer_multiaddr: "/ble/MAC:AA:BB:CC:DD:EE:FF/p2p/..." format.

        Returns:
            Connected BleakClient (the "stream" object).
        """
        # Parse MAC address from multiaddr
        parts = peer_multiaddr.split("/")
        mac = ""
        for part in parts:
            if part.startswith("MAC:"):
                mac = part[4:]  # Strip "MAC:" prefix
                break

        if not mac:
            raise ValueError(f"Cannot parse BLE MAC from multiaddr: {peer_multiaddr}")

        client = BleakClient(mac)
        await client.start_notify(QUBES_BLE_NOTIFY_CHAR, self._notification_handler)
        await client.connect()

        # Initialize receive buffer for this connection
        self._receive_buffers[mac] = bytearray()
        self._receive_events[mac] = asyncio.Event()

        logger.info("ble_connected", mac=mac)
        return client

    async def send(self, stream: Any, data: bytes) -> None:
        """
        Send data over a connected BLE stream.
        Fragments data into CHUNK_SIZE pieces with sequence framing.

        Args:
            stream: Connected BleakClient from connect().
            data: Raw bytes to send.
        """
        fragments = [data[i:i + CHUNK_SIZE] for i in range(0, len(data), CHUNK_SIZE)]
        total = len(fragments)

        for seq, fragment in enumerate(fragments):
            # Header: [seq uint32][total uint32][data]
            header = struct.pack(">II", seq, total)
            packet = header + fragment
            await stream.write_gatt_char(QUBES_BLE_WRITE_CHAR, packet, response=True)

        mac = stream.address
        logger.debug("ble_sent", mac=mac[:11], bytes=len(data), fragments=total)

    async def receive(self, stream: Any) -> bytes:
        """
        Wait for and return the next complete message from a BLE stream.
        Reassembles fragmented packets.

        Args:
            stream: Connected BleakClient from connect().

        Returns:
            Reassembled message bytes.
        """
        mac = stream.address
        event = self._receive_events.get(mac)
        if event:
            await event.wait()
            event.clear()

        data = bytes(self._receive_buffers.get(mac, bytearray()))
        self._receive_buffers[mac] = bytearray()
        return data

    def _notification_handler(self, sender: Any, data: bytes) -> None:
        """GATT notification callback — accumulates fragments and signals completion."""
        try:
            if len(data) < 8:
                return
            seq, total = struct.unpack_from(">II", data, 0)
            payload = data[8:]

            # Use sender as key (may be characteristic UUID or address depending on platform)
            key = str(sender)

            if key not in self._receive_buffers:
                self._receive_buffers[key] = bytearray()
                self._receive_events[key] = asyncio.Event()

            self._receive_buffers[key].extend(payload)

            # Signal complete when last fragment received
            if seq == total - 1:
                event = self._receive_events.get(key)
                if event:
                    event.set()

        except Exception as exc:
            logger.debug("ble_notification_error", error=str(exc))


def is_ble_available() -> bool:
    """Return True if bleak is installed and BLE transport can be used."""
    return _BLEAK_AVAILABLE


def is_ble_server_available() -> bool:
    """Return True if bless is installed and BLE advertising is supported."""
    return _BLESS_AVAILABLE


class BLEServer:
    """
    BLE GATT peripheral (server/advertise) role for Qubes relay.

    Advertises the Qubes relay service UUID so nearby devices running
    BLETransport can discover this node and connect without internet.
    This is the missing piece for BitChat-parity BLE mesh.

    Requires: pip install bless>=0.2.5

    Usage:
        server = BLEServer(peer_id="QmMyPeerID", on_message=handle_message)
        await server.start()
        # ... relay is now discoverable via BLE scan ...
        await server.stop()

    on_message(sender_addr: str, data: bytes) is called for each complete
    reassembled message received from a connecting BLE client.
    """

    def __init__(
        self,
        peer_id: str,
        on_message: Optional[Callable[[str, bytes], None]] = None,
    ) -> None:
        if not _BLESS_AVAILABLE:
            raise ImportError(
                "bless is required for BLE server mode. Install with: pip install bless>=0.2.5"
            )
        self.peer_id = peer_id
        self.on_message = on_message
        self._server: Optional[Any] = None
        self._running = False
        self._receive_buffers: Dict[str, bytearray] = {}
        self._receive_seqs: Dict[str, int] = {}

    async def start(self) -> None:
        """Start advertising the Qubes BLE relay service."""
        loop = asyncio.get_event_loop()
        self._server = BlessServer(name=f"Qubes-{self.peer_id[:8]}", loop=loop)
        self._server.read_request_func = self._read_request
        self._server.write_request_func = self._write_request

        await self._server.add_new_service(QUBES_BLE_SERVICE_UUID)

        # Write characteristic: remote → us (receive incoming data)
        write_props = GATTCharacteristicProperties.write | GATTCharacteristicProperties.write_without_response
        write_perms = GATTAttributePermissions.writeable
        await self._server.add_new_characteristic(
            QUBES_BLE_SERVICE_UUID,
            QUBES_BLE_WRITE_CHAR,
            write_props,
            None,
            write_perms,
        )

        # Notify characteristic: us → remote (send outgoing data)
        notify_props = GATTCharacteristicProperties.notify | GATTCharacteristicProperties.read
        notify_perms = GATTAttributePermissions.readable
        await self._server.add_new_characteristic(
            QUBES_BLE_SERVICE_UUID,
            QUBES_BLE_NOTIFY_CHAR,
            notify_props,
            None,
            notify_perms,
        )

        await self._server.start()
        self._running = True
        logger.info("ble_server_started", peer_id=self.peer_id[:16], service=QUBES_BLE_SERVICE_UUID)

    async def stop(self) -> None:
        """Stop BLE advertising and disconnect all clients."""
        if self._server and self._running:
            await self._server.stop()
            self._running = False
            logger.info("ble_server_stopped")

    async def notify_all(self, data: bytes) -> None:
        """Send data to all connected BLE clients via GATT notify."""
        if not self._server or not self._running:
            return
        fragments = [data[i:i + CHUNK_SIZE] for i in range(0, len(data), CHUNK_SIZE)]
        total = len(fragments)
        for seq, fragment in enumerate(fragments):
            packet = struct.pack(">II", seq, total) + fragment
            self._server.get_characteristic(QUBES_BLE_NOTIFY_CHAR).value = bytearray(packet)
            self._server.update_value(QUBES_BLE_SERVICE_UUID, QUBES_BLE_NOTIFY_CHAR)
            await asyncio.sleep(0.01)  # yield between fragments

    def _read_request(self, characteristic: Any, **kwargs: Any) -> bytearray:
        """GATT read handler — returns empty payload (notify-only design)."""
        return bytearray()

    def _write_request(self, characteristic: Any, value: Any, **kwargs: Any) -> None:
        """GATT write handler — reassemble fragments from BLE client."""
        try:
            data = bytes(value)
            if len(data) < 8:
                return
            seq, total = struct.unpack_from(">II", data, 0)
            payload = data[8:]

            # Use characteristic handle as key (single-client simplification)
            key = "default"
            if key not in self._receive_buffers:
                self._receive_buffers[key] = bytearray()

            self._receive_buffers[key].extend(payload)

            if seq == total - 1:
                complete = bytes(self._receive_buffers.pop(key))
                logger.debug("ble_server_received", bytes=len(complete))
                if self.on_message:
                    try:
                        self.on_message("ble_client", complete)
                    except Exception as exc:
                        logger.debug("ble_server_message_handler_error", error=str(exc))
        except Exception as exc:
            logger.debug("ble_server_write_error", error=str(exc))
