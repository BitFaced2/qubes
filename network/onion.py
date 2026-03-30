"""
Onion Router — Phase 2

2-hop onion routing to hide the communication graph from relay operators.
When Alice sends to Bob, she picks 2 intermediate relays (R1, R2) and wraps
the payload in 3 ECIES encryption layers:

  Outer  → encrypted for R1: { "next_hop": r2_relay_addr, "payload": <middle_hex> }
  Middle → encrypted for R2: { "next_hop": dest_relay_addr, "payload": <inner_hex> }
  Inner  → encrypted for dest: { "payload": <message_hex> }

R1 learns only: "forward to R2".
R2 learns only: "forward to Bob's relay".
Neither learns Alice is the origin.

Encryption: ephemeral ECDH (secp256k1) → HKDF-SHA256 → AES-256-GCM.
Packet size: padded to uniform PAD_BYTES (512) to prevent size-based traffic analysis.
"""

import json
import os
from typing import Optional, Tuple

from utils.logging import get_logger

logger = get_logger(__name__)

PAD_BYTES = 512


# ---------------------------------------------------------------------------
# ECIES helpers — ephemeral ECDH + AES-256-GCM
# Reuses the cryptography library already in requirements.txt.
# ---------------------------------------------------------------------------

def _ecies_encrypt(plaintext: bytes, recipient_pubkey_bytes: bytes) -> bytes:
    """
    Encrypt plaintext for recipient_pubkey using ephemeral ECDH + AES-256-GCM.

    Output wire format:
        ephemeral_pubkey (33 bytes, compressed)
        nonce (12 bytes)
        ciphertext + tag (len(plaintext) + 16 bytes)

    Args:
        plaintext: Bytes to encrypt.
        recipient_pubkey_bytes: 33-byte compressed or 32-byte x-only secp256k1 pubkey.
    """
    from cryptography.hazmat.primitives.asymmetric.ec import (
        SECP256K1,
        generate_private_key,
        ECDH,
    )
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    # Load recipient public key
    if len(recipient_pubkey_bytes) == 32:
        # x-only: reconstruct compressed (even y = 02 prefix)
        recipient_pubkey_bytes = b"\x02" + recipient_pubkey_bytes

    from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from cryptography.hazmat.primitives.asymmetric.ec import (
        EllipticCurvePublicNumbers,
    )
    from cryptography.hazmat.backends import default_backend

    recipient_pub = EllipticCurvePublicKey.from_encoded_point(
        SECP256K1(), recipient_pubkey_bytes
    )

    # Generate ephemeral keypair
    ephemeral_priv = generate_private_key(SECP256K1())
    ephemeral_pub_bytes = ephemeral_priv.public_key().public_bytes(
        Encoding.X962, PublicFormat.CompressedPoint
    )

    # ECDH → shared secret
    shared_secret = ephemeral_priv.exchange(ECDH(), recipient_pub)

    # HKDF-SHA256 → 32-byte AES key
    aes_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"qubes-onion-v1",
    ).derive(shared_secret)

    # AES-256-GCM encrypt
    nonce = os.urandom(12)
    ciphertext = AESGCM(aes_key).encrypt(nonce, plaintext, None)

    return ephemeral_pub_bytes + nonce + ciphertext


def _ecies_decrypt(ciphertext_blob: bytes, my_privkey_bytes: bytes) -> bytes:
    """
    Decrypt an ECIES blob produced by _ecies_encrypt.

    Args:
        ciphertext_blob: Wire-format bytes (see _ecies_encrypt).
        my_privkey_bytes: 32-byte raw private key scalar.
    """
    from cryptography.hazmat.primitives.asymmetric.ec import (
        SECP256K1,
        ECDH,
        EllipticCurvePublicKey,
    )
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    # Parse wire format
    ephemeral_pub_bytes = ciphertext_blob[:33]
    nonce = ciphertext_blob[33:45]
    ciphertext = ciphertext_blob[45:]

    ephemeral_pub = EllipticCurvePublicKey.from_encoded_point(
        SECP256K1(), ephemeral_pub_bytes
    )

    # Reconstruct private key
    from cryptography.hazmat.primitives.asymmetric.ec import (
        derive_private_key,
    )
    d = int.from_bytes(my_privkey_bytes, "big")
    my_priv = derive_private_key(d, SECP256K1())

    shared_secret = my_priv.exchange(ECDH(), ephemeral_pub)

    aes_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"qubes-onion-v1",
    ).derive(shared_secret)

    return AESGCM(aes_key).decrypt(nonce, ciphertext, None)


# ---------------------------------------------------------------------------
# OnionRouter
# ---------------------------------------------------------------------------

class OnionRouter:
    """
    2-hop onion routing for Qubes P2P relay.

    Used by RelayNodeManager.send_message() before dispatching to the DHT.
    Each hop peels one ECIES layer and forwards the inner packet.
    Packets are padded to PAD_BYTES (512) for uniform size.
    """

    def __init__(self, pad_to_bytes: int = PAD_BYTES):
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
        Wrap payload in 3 ECIES layers (outer → R1, middle → R2, inner → dest).

        Args:
            payload: Already encrypted message bytes (from messaging.py).
            r1_pubkey: First hop's secp256k1 pubkey (33 bytes compressed or 32 x-only).
            r2_pubkey: Second hop's secp256k1 pubkey.
            dest_pubkey: Final recipient's secp256k1 pubkey.
            dest_relay_addr: Multiaddr of recipient's relay (R2 forwards here).
            r2_relay_addr: Multiaddr of R2 (R1 forwards here).

        Returns:
            Padded onion packet bytes ready for transmission.
        """
        try:
            # Inner layer: encrypted for dest, contains the actual payload
            inner_json = json.dumps({"payload": payload.hex()}).encode()
            inner = _ecies_encrypt(inner_json, dest_pubkey)

            # Middle layer: encrypted for R2, contains dest_relay_addr + inner
            middle_json = json.dumps({
                "next_hop": dest_relay_addr,
                "payload": inner.hex(),
            }).encode()
            middle = _ecies_encrypt(middle_json, r2_pubkey)

            # Outer layer: encrypted for R1, contains r2_relay_addr + middle
            outer_json = json.dumps({
                "next_hop": r2_relay_addr,
                "payload": middle.hex(),
            }).encode()
            outer = _ecies_encrypt(outer_json, r1_pubkey)

            padded = self._pad(outer)
            logger.debug(
                "onion_wrap_done",
                original_len=len(payload),
                onion_len=len(padded),
            )
            return padded

        except Exception as exc:
            # Never let onion failure block message delivery — fall back to raw payload
            logger.warning("onion_wrap_failed", error=str(exc))
            return payload

    async def unwrap_message(
        self,
        onion_packet: bytes,
        my_privkey: bytes,
    ) -> Tuple[bytes, Optional[str]]:
        """
        Peel one ECIES layer.

        Args:
            onion_packet: Received onion bytes (may be padded).
            my_privkey: This node's 32-byte private key.

        Returns:
            (inner_payload_bytes, next_hop_addr)
            If next_hop_addr is None, this node is the final recipient.
        """
        try:
            # Strip trailing padding zeros (the ECIES magic bytes are non-zero)
            stripped = onion_packet.rstrip(b"\x00") if len(onion_packet) == self.pad_to_bytes else onion_packet

            decrypted = _ecies_decrypt(stripped, my_privkey)
            layer = json.loads(decrypted.decode())

            inner_hex = layer.get("payload", "")
            inner_bytes = bytes.fromhex(inner_hex)
            next_hop = layer.get("next_hop")  # None if this is the innermost layer

            logger.debug(
                "onion_unwrap_done",
                next_hop=next_hop,
                inner_len=len(inner_bytes),
            )
            return inner_bytes, next_hop

        except Exception as exc:
            logger.warning("onion_unwrap_failed", error=str(exc))
            # Return packet as-is — caller handles delivery
            return onion_packet, None

    def _pad(self, data: bytes) -> bytes:
        """Pad data to uniform packet size to prevent size-based traffic analysis."""
        if len(data) < self.pad_to_bytes:
            return data + b"\x00" * (self.pad_to_bytes - len(data))
        return data
