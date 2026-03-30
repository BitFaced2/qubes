"""
libp2p-daemon Bridge Implementation

Bridges to Go libp2p-daemon for stable DHT discovery and GossipSub messaging.
Uses subprocess + protobuf/JSON IPC for communication.

Architecture:
- Spawns libp2p-daemon process with DHT and GossipSub enabled
- Communicates via control socket (multiaddr)
- Provides Python interface matching libp2p API surface area
"""

import asyncio
import json
import socket
import struct
from typing import Optional, Dict, List, Any
from pathlib import Path
from dataclasses import dataclass
import subprocess

from core.exceptions import NetworkError
from utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PeerInfo:
    """Information about a peer"""
    peer_id: str
    addrs: List[str]


class LibP2PDaemonBridge:
    """
    Bridge to Go libp2p-daemon for stable P2P networking

    Provides:
    - DHT-based peer discovery (Kademlia)
    - GossipSub message propagation
    - Connection management
    - Peer routing

    Architecture:
    1. Spawn libp2p-daemon subprocess
    2. Connect to control socket
    3. Send/receive protobuf or JSON messages
    4. Translate to Python-friendly API
    """

    def __init__(
        self,
        qube_id: str,
        listen_port: int = 0,  # 0 = auto-assign
        control_socket_path: Optional[str] = None,
        bootstrap_peers: Optional[List[str]] = None,
        daemon_binary: Optional[str] = None
    ):
        """
        Initialize libp2p-daemon bridge

        Args:
            qube_id: Qube ID (used as content routing key)
            listen_port: Port for libp2p to listen on (0 = auto)
            control_socket_path: Path to control socket (default: /tmp/libp2p-daemon-{qube_id}.sock)
            bootstrap_peers: List of bootstrap peer multiaddrs
            daemon_binary: Path to libp2p-daemon binary (searches PATH if None)
        """
        self.qube_id = qube_id
        self.listen_port = listen_port
        self.bootstrap_peers = bootstrap_peers or []

        # Control socket path
        if control_socket_path:
            self.control_socket_path = control_socket_path
        else:
            # Use temp directory for socket
            import tempfile
            temp_dir = Path(tempfile.gettempdir())
            self.control_socket_path = str(temp_dir / f"libp2p-daemon-{qube_id}.sock")

        # Daemon binary path
        self.daemon_binary = daemon_binary or "p2pd"  # Assumes in PATH

        # Process and connection state
        self.daemon_process: Optional[subprocess.Popen] = None
        self.control_socket: Optional[socket.socket] = None
        self.is_running = False

        # Network state
        self.peer_id: Optional[str] = None
        self.multiaddr: Optional[str] = None
        self.known_peers: Dict[str, PeerInfo] = {}

        logger.info(
            "libp2p_daemon_bridge_initialized",
            qube_id=qube_id,
            control_socket=self.control_socket_path
        )

    async def start(self) -> None:
        """
        Start libp2p-daemon and establish control connection

        Steps:
        1. Spawn libp2p-daemon process
        2. Wait for control socket to be ready
        3. Connect to control socket
        4. Initialize DHT and GossipSub
        """
        try:
            logger.info("starting_libp2p_daemon", qube_id=self.qube_id)

            # Build daemon command
            # WebSocket and QUIC ports derived from TCP port
            ws_port = self.listen_port + 1 if self.listen_port else 4002
            quic_port = self.listen_port if self.listen_port else 4001

            cmd = [
                self.daemon_binary,
                f"-listen=/ip4/0.0.0.0/tcp/{self.listen_port}",
                f"-listen=/ip4/0.0.0.0/tcp/{ws_port}/ws",    # WebSocket (browser + iOS)
                f"-listen=/ip4/0.0.0.0/udp/{quic_port}/quic", # QUIC (mobile connection migration)
                f"-controlSocket={self.control_socket_path}",
                "-dht",          # Enable Kademlia DHT
                "-dhtClient=false",  # Full DHT server (provide + route)
                "-gossipsub",    # Enable GossipSub pub/sub
                "-natPortMap",   # UPnP/NAT-PMP port mapping
                "-autoRelay",    # Act as circuit relay for NATed peers
            ]

            # Add bootstrap peers
            for peer in self.bootstrap_peers:
                cmd.append(f"-bootstrapPeer={peer}")

            logger.debug("spawning_daemon_process", cmd=" ".join(cmd))

            # Spawn daemon process
            self.daemon_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            # Wait for control socket to be created
            await self._wait_for_socket()

            # Connect to control socket
            await self._connect_control_socket()

            # Get our peer ID and multiaddr
            await self._identify()

            # Announce ourselves to DHT
            await self._announce_to_dht()

            self.is_running = True

            logger.info(
                "libp2p_daemon_started",
                qube_id=self.qube_id,
                peer_id=self.peer_id,
                multiaddr=self.multiaddr
            )

        except FileNotFoundError:
            raise NetworkError(
                f"libp2p-daemon binary '{self.daemon_binary}' not found. "
                f"Install from: https://github.com/libp2p/go-libp2p-daemon",
                context={"qube_id": self.qube_id}
            )
        except Exception as e:
            logger.error("libp2p_daemon_start_failed", qube_id=self.qube_id, exc_info=True)
            await self.stop()  # Cleanup on failure
            raise NetworkError(
                f"Failed to start libp2p-daemon: {str(e)}",
                context={"qube_id": self.qube_id},
                cause=e
            )

    async def stop(self) -> None:
        """Stop libp2p-daemon and cleanup resources"""
        try:
            logger.info("stopping_libp2p_daemon", qube_id=self.qube_id)

            # Close control socket
            if self.control_socket:
                self.control_socket.close()
                self.control_socket = None

            # Terminate daemon process
            if self.daemon_process:
                self.daemon_process.terminate()
                try:
                    self.daemon_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning("daemon_process_force_kill")
                    self.daemon_process.kill()
                    self.daemon_process.wait()

                self.daemon_process = None

            # Cleanup socket file
            socket_path = Path(self.control_socket_path)
            if socket_path.exists():
                socket_path.unlink()

            self.is_running = False

            logger.info("libp2p_daemon_stopped", qube_id=self.qube_id)

        except Exception as e:
            logger.error("libp2p_daemon_stop_failed", exc_info=True)

    async def _wait_for_socket(self, timeout: float = 10.0) -> None:
        """Wait for control socket file to be created"""
        socket_path = Path(self.control_socket_path)
        deadline = asyncio.get_event_loop().time() + timeout

        while asyncio.get_event_loop().time() < deadline:
            if socket_path.exists():
                logger.debug("control_socket_ready", path=self.control_socket_path)
                return

            await asyncio.sleep(0.1)

        raise TimeoutError(f"Control socket not created within {timeout}s")

    async def _connect_control_socket(self) -> None:
        """Connect to libp2p-daemon control socket"""
        try:
            self.control_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.control_socket.connect(self.control_socket_path)
            self.control_socket.settimeout(5.0)

            logger.debug("control_socket_connected", path=self.control_socket_path)

        except Exception as e:
            raise NetworkError(
                f"Failed to connect to control socket: {str(e)}",
                context={"socket_path": self.control_socket_path},
                cause=e
            )

    async def _send_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send request to libp2p-daemon control socket

        Uses length-prefixed JSON protocol:
        - 4 bytes: message length (big-endian uint32)
        - N bytes: JSON message

        Args:
            request: Request dict

        Returns:
            Response dict
        """
        try:
            # Serialize request
            request_json = json.dumps(request).encode("utf-8")
            request_length = struct.pack(">I", len(request_json))

            # Send request
            self.control_socket.sendall(request_length + request_json)

            # Read response length
            response_length_bytes = self._recv_exactly(4)
            response_length = struct.unpack(">I", response_length_bytes)[0]

            # Read response
            response_json = self._recv_exactly(response_length)
            response = json.loads(response_json.decode("utf-8"))

            return response

        except Exception as e:
            logger.error("control_socket_request_failed", request_type=request.get("type"), exc_info=True)
            raise NetworkError(
                f"Control socket request failed: {str(e)}",
                context={"request": request},
                cause=e
            )

    def _recv_exactly(self, n: int) -> bytes:
        """Receive exactly n bytes from socket"""
        data = b""
        while len(data) < n:
            chunk = self.control_socket.recv(n - len(data))
            if not chunk:
                raise ConnectionError("Socket connection closed")
            data += chunk
        return data

    async def _identify(self) -> None:
        """Get our peer ID and multiaddr from daemon"""
        response = await self._send_request({"type": "IDENTIFY"})

        if response.get("type") == "IDENTIFY_RESPONSE":
            self.peer_id = response.get("peerId")
            addrs = response.get("addrs", [])
            if addrs:
                self.multiaddr = addrs[0]  # Use first address

            logger.debug(
                "daemon_identified",
                peer_id=self.peer_id,
                multiaddr=self.multiaddr
            )
        else:
            raise NetworkError(
                f"Unexpected identify response: {response}",
                context={"response": response}
            )

    async def _announce_to_dht(self) -> None:
        """Announce ourselves to DHT"""
        try:
            # Provide our Qube ID as content in the DHT
            response = await self._send_request({
                "type": "DHT_PROVIDE",
                "cid": self.qube_id  # Use Qube ID as content identifier
            })

            if response.get("type") == "OK":
                logger.info("announced_to_dht", qube_id=self.qube_id)
            else:
                logger.warning("dht_announce_failed", response=response)

        except Exception as e:
            logger.warning("dht_announce_error", error=str(e))
            # Non-fatal - DHT may not be ready yet

    async def find_peer(self, qube_id: str, timeout: float = 30.0) -> Optional[PeerInfo]:
        """
        Find peer by Qube ID via DHT

        Args:
            qube_id: Target Qube ID
            timeout: Search timeout in seconds

        Returns:
            PeerInfo if found, None otherwise
        """
        try:
            logger.debug("searching_dht_for_qube", target_qube_id=qube_id)

            response = await self._send_request({
                "type": "DHT_FIND_PROVIDERS",
                "cid": qube_id,
                "numProviders": 1
            })

            if response.get("type") == "DHT_PROVIDERS":
                providers = response.get("providers", [])

                if providers:
                    provider = providers[0]
                    peer_info = PeerInfo(
                        peer_id=provider.get("peerId"),
                        addrs=provider.get("addrs", [])
                    )

                    # Cache peer info
                    self.known_peers[qube_id] = peer_info

                    logger.info(
                        "qube_found_in_dht",
                        target_qube_id=qube_id,
                        peer_id=peer_info.peer_id,
                        addrs=peer_info.addrs
                    )

                    return peer_info
                else:
                    logger.debug("qube_not_found_in_dht", target_qube_id=qube_id)
                    return None

            else:
                logger.warning("unexpected_dht_response", response=response)
                return None

        except Exception as e:
            logger.error("dht_find_peer_failed", target_qube_id=qube_id, error=str(e))
            return None

    async def connect_peer(self, peer_info: PeerInfo) -> bool:
        """
        Connect to peer

        Args:
            peer_info: Peer information

        Returns:
            True if connected successfully
        """
        try:
            response = await self._send_request({
                "type": "CONNECT",
                "peerId": peer_info.peer_id,
                "addrs": peer_info.addrs
            })

            if response.get("type") == "OK":
                logger.info("peer_connected", peer_id=peer_info.peer_id)
                return True
            else:
                logger.warning("peer_connect_failed", response=response)
                return False

        except Exception as e:
            logger.error("peer_connect_error", peer_id=peer_info.peer_id, error=str(e))
            return False

    async def publish_to_topic(self, topic: str, data: bytes) -> bool:
        """
        Publish message to GossipSub topic

        Args:
            topic: Topic name
            data: Message data (bytes)

        Returns:
            True if published successfully
        """
        try:
            import base64

            response = await self._send_request({
                "type": "PUBSUB_PUBLISH",
                "topic": topic,
                "data": base64.b64encode(data).decode("ascii")
            })

            if response.get("type") == "OK":
                logger.debug("message_published_to_topic", topic=topic, size=len(data))
                return True
            else:
                logger.warning("pubsub_publish_failed", response=response)
                return False

        except Exception as e:
            logger.error("pubsub_publish_error", topic=topic, error=str(e))
            return False

    async def subscribe_to_topic(self, topic: str) -> bool:
        """
        Subscribe to GossipSub topic

        Args:
            topic: Topic name

        Returns:
            True if subscribed successfully
        """
        try:
            response = await self._send_request({
                "type": "PUBSUB_SUBSCRIBE",
                "topic": topic
            })

            if response.get("type") == "OK":
                logger.info("subscribed_to_topic", topic=topic)
                return True
            else:
                logger.warning("pubsub_subscribe_failed", response=response)
                return False

        except Exception as e:
            logger.error("pubsub_subscribe_error", topic=topic, error=str(e))
            return False

    async def get_topic_messages(self, topic: str) -> List[bytes]:
        """
        Get messages from subscribed topic

        Note: This is a simplified implementation. In production, you'd
        want a separate message handler thread/task.

        Args:
            topic: Topic name

        Returns:
            List of message data
        """
        try:
            response = await self._send_request({
                "type": "PUBSUB_GET_MESSAGES",
                "topic": topic
            })

            if response.get("type") == "PUBSUB_MESSAGES":
                import base64
                messages = response.get("messages", [])
                return [base64.b64decode(msg["data"]) for msg in messages]
            else:
                return []

        except Exception as e:
            logger.error("pubsub_get_messages_error", topic=topic, error=str(e))
            return []

    def get_network_info(self) -> Dict[str, Any]:
        """Get network information"""
        return {
            "qube_id": self.qube_id,
            "peer_id": self.peer_id,
            "multiaddr": self.multiaddr,
            "is_running": self.is_running,
            "control_socket": self.control_socket_path,
            "known_peers_count": len(self.known_peers),
            "bootstrap_peers": self.bootstrap_peers
        }


# NOTE: This is a simplified implementation for demonstration
# For production, install the official Python client:
#   pip install p2pclient
#
# Then use:
#   from p2pclient import Client
#   client = Client()
#   await client.listen()
#   await client.dht_find_peer(peer_id)
#
# See: https://github.com/mhchia/py-libp2p-daemon-bindings
#
# The above implementation provides the same interface but uses a
# simplified JSON-over-socket protocol. To use the real protobuf protocol,
# replace this module with p2pclient.
