"""
Nostr Transport — Phase 3 (Scaffold)

Uses public Nostr relays as a Priority-4 transport fallback when the DHT
is unreachable (no internet, ISP blocks libp2p ports, etc.).

Privacy design (BitChat-style with maximum privacy):
  - Ephemeral keypair per session — NOT the user's BCH wallet keypair.
    The Nostr identity is mathematically unlinked from the BCH / Qube identity.
  - NIP-44 encrypted DMs (ChaCha20-Poly1305 + secp256k1 ECDH key agreement).
  - Broadcast to all configured Nostr relays simultaneously.
  - Recipient identified by a session-derived ephemeral pubkey known only to
    sender (derived via HKDF(qube_privkey, recipient_qube_id, session_id)).
  - An observer watching Nostr relays sees encrypted blobs from disposable
    ephemeral pubkeys — no link to real BCH/Qube identities.

Nostr relay list comes from EndpointPreferences.nostr_relays (Settings → Endpoints).

TODO Phase 3: Implement WebSocket connections, NIP-44 encryption, event publishing.
"""

import hashlib
import hmac
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils.logging import get_logger

logger = get_logger(__name__)

# NIP-44 version tag
NIP44_VERSION = b"\x02"


class NostrTransport:
    """
    Nostr relay transport with NIP-44 + ephemeral keypairs.

    Phase 3 TODO:
    - Open WebSocket connections to each relay in relay_urls
    - Subscribe to ephemeral inbox pubkey (NIP-01 REQ filter)
    - Publish NIP-44 encrypted DMs via EVENT messages
    - Handle reconnection, relay failures, and message deduplication
    """

    def __init__(self, relay_urls: List[str]):
        """
        Args:
            relay_urls: List of Nostr relay WebSocket URLs.
                        Defaults to EndpointPreferences.nostr_relays if empty.
        """
        self.relay_urls = relay_urls
        self._connections: Dict[str, Any] = {}  # url → WebSocket (Phase 3)
        self._running = False

    async def start(self) -> None:
        """Connect to all configured Nostr relays. TODO Phase 3."""
        logger.info("nostr_transport_start_stub", relay_count=len(self.relay_urls))

    async def stop(self) -> None:
        """Disconnect from all Nostr relays. TODO Phase 3."""
        self._running = False
        logger.info("nostr_transport_stopped")

    async def send(
        self,
        recipient_ephemeral_pubkey: str,
        encrypted_payload: bytes,
    ) -> bool:
        """
        Publish a NIP-44 encrypted DM to all relays.

        Args:
            recipient_ephemeral_pubkey: secp256k1 hex pubkey of recipient's ephemeral inbox.
            encrypted_payload: Already encrypted bytes (ECIES from network/messaging.py).

        TODO Phase 3: wrap in NIP-44, publish as Nostr EVENT kind 4.
        """
        logger.info(
            "nostr_send_stub",
            recipient_key=recipient_ephemeral_pubkey[:16] + "...",
            payload_len=len(encrypted_payload),
        )
        return False  # Phase 3 TODO

    async def listen(
        self,
        my_ephemeral_privkey: str,
        handler: Callable[[bytes], None],
    ) -> None:
        """
        Subscribe to incoming messages for my_ephemeral_privkey.

        Args:
            my_ephemeral_privkey: secp256k1 hex privkey of our ephemeral inbox.
            handler: Callback called with decrypted payload bytes on each message.

        TODO Phase 3: NIP-01 REQ subscription + NIP-44 decryption.
        """
        logger.info("nostr_listen_stub")

    @staticmethod
    def derive_session_keypair(
        qube_privkey_bytes: bytes,
        recipient_qube_id: str,
        session_id: str,
    ) -> Tuple[bytes, bytes]:
        """
        Derive an ephemeral (privkey, pubkey) pair for a session.

        Uses HKDF so the Nostr keypair is deterministic per session but
        completely unlinkable to the BCH wallet keypair by an outside observer.

        Args:
            qube_privkey_bytes: Raw bytes of the Qube's secp256k1 private key.
            recipient_qube_id: Recipient's 8-char Qube ID.
            session_id: Unique session identifier (e.g., conversation_id).

        Returns:
            (ephemeral_privkey_bytes, ephemeral_pubkey_bytes)

        TODO Phase 3: replace stub HKDF with proper secp256k1 key derivation.
        """
        # HKDF-SHA256 stub
        ikm = qube_privkey_bytes
        info = f"qubes-nostr-session:{recipient_qube_id}:{session_id}".encode()
        prk = hmac.new(b"qubes-nostr", ikm, hashlib.sha256).digest()
        ephemeral_privkey = hmac.new(prk, info + b"\x01", hashlib.sha256).digest()
        # TODO Phase 3: derive secp256k1 pubkey from ephemeral_privkey
        ephemeral_pubkey = b"\x00" * 33  # placeholder
        return ephemeral_privkey, ephemeral_pubkey
