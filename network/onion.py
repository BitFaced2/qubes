"""
Onion Router — Phase 2 (Scaffold)

2-hop onion routing to hide the communication graph from relay operators.
When Alice sends to Bob, she picks 2 intermediate relays (R1, R2) and wraps
the payload in 3 encryption layers:

  Outer  → encrypted for R1, contains: addr of R2 + middle layer
  Middle → encrypted for R2, contains: addr of Bob's relay + inner layer
  Inner  → encrypted for Bob, contains: actual message payload

R1 learns only: "forward to R2". R2 learns only: "forward to Bob's relay".
Neither learns Alice is the origin. Bob's relay learns only the final destination.

TODO Phase 2: Implement wrap_message / unwrap_message using Noise XX handshake.
"""

from typing import Optional, Tuple
from utils.logging import get_logger

logger = get_logger(__name__)


class OnionRouter:
    """
    2-hop onion routing for Qubes P2P relay.

    Used by RelayNodeManager.send_message() before dispatching to the DHT.

    Phase 2 TODO:
    - Select 2 random relay nodes from DHT routing table as R1, R2
    - Wrap payload in 3 Noise-encrypted layers (outer for R1, middle for R2, inner for Bob)
    - Pad all packets to uniform size (512 bytes default)
    - Implement unwrap_message for relay nodes that need to forward
    """

    def __init__(self, pad_to_bytes: int = 512):
        self.pad_to_bytes = pad_to_bytes

    async def wrap_message(
        self,
        payload: bytes,
        r1_pubkey: bytes,
        r2_pubkey: bytes,
        dest_pubkey: bytes,
        dest_relay_addr: str,
        r2_relay_addr: str,
    ) -> bytes:
        """
        Wrap payload in 2-hop onion layers.

        TODO Phase 2: implement Noise XX multi-layer encryption.
        """
        logger.debug("onion_wrap_todo", payload_len=len(payload))
        # Stub: return payload unmodified until Phase 2
        return payload

    async def unwrap_message(
        self,
        onion_packet: bytes,
        my_privkey: bytes,
    ) -> Tuple[bytes, Optional[str]]:
        """
        Peel one onion layer.

        Returns:
            (inner_payload, next_hop_addr) — forward inner to next_hop if not None.
            If next_hop is None, this node is the final recipient.

        TODO Phase 2: implement Noise XX layer decryption.
        """
        logger.debug("onion_unwrap_todo", packet_len=len(onion_packet))
        # Stub: treat packet as plain payload, no next hop
        return onion_packet, None

    def _pad(self, data: bytes) -> bytes:
        """Pad data to uniform packet size to prevent size-based traffic analysis."""
        if len(data) < self.pad_to_bytes:
            return data + b"\x00" * (self.pad_to_bytes - len(data))
        return data
