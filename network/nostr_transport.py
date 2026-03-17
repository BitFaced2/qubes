"""
Nostr Transport — Phase 3

Uses public Nostr relays as a Priority-4 transport fallback when the DHT
is unreachable (no internet, ISP blocks libp2p ports, etc.).

Privacy design (BitChat-style):
  - Ephemeral keypair per session — NOT the user's BCH wallet keypair.
    The Nostr identity is mathematically unlinked from the BCH / Qube identity.
  - NIP-44 v2 encrypted DMs (XChaCha20-Poly1305 + secp256k1 ECDH).
  - Broadcast to all configured Nostr relays simultaneously.
  - Recipient identified by a session-derived ephemeral pubkey known only
    to the sender (derived via HKDF from qube_privkey + session context).
  - Observer sees encrypted blobs from disposable ephemeral pubkeys —
    no link to real BCH/Qube identities.

Nostr relay list comes from EndpointPreferences.nostr_relays (Settings → Endpoints).
"""

import asyncio
import base64
import hashlib
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# secp256k1 curve constants
# ---------------------------------------------------------------------------
_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)


def _point_add(P: Optional[tuple], Q: Optional[tuple]) -> Optional[tuple]:
    if P is None:
        return Q
    if Q is None:
        return P
    if P[0] == Q[0]:
        if P[1] != Q[1]:
            return None
        lam = (3 * P[0] * P[0] * pow(2 * P[1], _P - 2, _P)) % _P
    else:
        lam = ((Q[1] - P[1]) * pow(Q[0] - P[0], _P - 2, _P)) % _P
    x = (lam * lam - P[0] - Q[0]) % _P
    y = (lam * (P[0] - x) - P[1]) % _P
    return (x, y)


def _point_mul(P: tuple, k: int) -> Optional[tuple]:
    R: Optional[tuple] = None
    while k:
        if k & 1:
            R = _point_add(R, P)
        P = _point_add(P, P)
        k >>= 1
    return R


def _lift_x(x: int) -> tuple:
    """Lift x-coordinate to (x, y) secp256k1 point with even y."""
    y_sq = (pow(x, 3, _P) + 7) % _P
    y = pow(y_sq, (_P + 1) // 4, _P)
    if (y * y) % _P != y_sq:
        raise ValueError("Point not on secp256k1 curve")
    return (x, y if y % 2 == 0 else _P - y)


def _tagged_hash(tag: str, data: bytes) -> bytes:
    h = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(h + h + data).digest()


def _privkey_to_xonly_pubkey(d: int) -> bytes:
    """Return 32-byte x-only pubkey from integer private key."""
    P = _point_mul(_G, d)
    return P[0].to_bytes(32, "big")


def _pubkey_bytes_to_point(pubkey: bytes) -> tuple:
    """
    Accept 32-byte x-only (BIP-340) or 33-byte compressed pubkey.
    Returns (x, y) curve point.
    """
    if len(pubkey) == 32:
        return _lift_x(int.from_bytes(pubkey, "big"))
    if len(pubkey) == 33:
        x = int.from_bytes(pubkey[1:], "big")
        y_sq = (pow(x, 3, _P) + 7) % _P
        y = pow(y_sq, (_P + 1) // 4, _P)
        if (y * y) % _P != y_sq:
            raise ValueError("Compressed pubkey not on curve")
        want_even = pubkey[0] == 0x02
        if (y % 2 == 0) != want_even:
            y = _P - y
        return (x, y)
    raise ValueError(f"Unsupported pubkey length: {len(pubkey)}")


# ---------------------------------------------------------------------------
# BIP-340 Schnorr signing (needed for valid Nostr events)
# ---------------------------------------------------------------------------

def _schnorr_sign(msg32: bytes, secret_key_int: int) -> bytes:
    """
    BIP-340 Schnorr signature.
    msg32 must be exactly 32 bytes (the SHA-256 event ID).
    """
    assert len(msg32) == 32
    P = _point_mul(_G, secret_key_int)
    # Ensure public key has even y
    d = (_N - secret_key_int) if P[1] % 2 != 0 else secret_key_int
    px = P[0].to_bytes(32, "big")

    # Deterministic nonce via BIP-340/nonce tagged hash
    a = d.to_bytes(32, "big")
    rand = _tagged_hash("BIP0340/nonce", a + px + msg32)
    k0 = int.from_bytes(rand, "big") % _N
    if k0 == 0:
        raise ValueError("Schnorr nonce is zero — this should never happen")

    R = _point_mul(_G, k0)
    k = (_N - k0) if R[1] % 2 != 0 else k0
    rx = R[0].to_bytes(32, "big")

    e = int.from_bytes(_tagged_hash("BIP0340/challenge", rx + px + msg32), "big") % _N
    s = (k + e * d) % _N
    return rx + s.to_bytes(32, "big")


# ---------------------------------------------------------------------------
# NIP-44 v2 encryption
# ---------------------------------------------------------------------------

def _nip44_conversation_key(our_privkey_int: int, their_pubkey_point: tuple) -> bytes:
    """ECDH + HKDF-SHA256 to derive a 32-byte NIP-44 conversation key."""
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes

    shared_point = _point_mul(their_pubkey_point, our_privkey_int)
    shared_x = shared_point[0].to_bytes(32, "big")

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"nip44-v2",
        info=b"",
    )
    return hkdf.derive(shared_x)


def _nip44_encrypt(plaintext: bytes, conversation_key: bytes) -> str:
    """
    Encrypt plaintext with NIP-44 v2.
    Uses XChaCha20-Poly1305 (24-byte nonce) when available in `cryptography`,
    falls back to ChaCha20-Poly1305 (12-byte nonce) for older builds.
    Both ends use the same version byte so decryption is symmetric.
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import XChaCha20Poly1305
        nonce = os.urandom(24)
        ct = XChaCha20Poly1305(conversation_key).encrypt(nonce, plaintext, None)
        raw = b"\x02" + nonce + ct          # version=2 (NIP-44 v2)
    except (ImportError, AttributeError):
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        nonce = os.urandom(12)
        ct = ChaCha20Poly1305(conversation_key).encrypt(nonce, plaintext, None)
        raw = b"\x01" + nonce + ct          # version=1 (Qubes fallback)
    return base64.b64encode(raw).decode()


def _nip44_decrypt(ciphertext_b64: str, conversation_key: bytes) -> bytes:
    """Decrypt NIP-44 ciphertext (version 1 or 2)."""
    raw = base64.b64decode(ciphertext_b64)
    version = raw[0]
    if version == 0x02:
        from cryptography.hazmat.primitives.ciphers.aead import XChaCha20Poly1305
        return XChaCha20Poly1305(conversation_key).decrypt(raw[1:25], raw[25:], None)
    if version == 0x01:
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        return ChaCha20Poly1305(conversation_key).decrypt(raw[1:13], raw[13:], None)
    raise ValueError(f"Unsupported NIP-44 version byte: {version}")


# ---------------------------------------------------------------------------
# Nostr event helpers
# ---------------------------------------------------------------------------

def _event_id(pubkey_hex: str, created_at: int, kind: int, tags: list, content: str) -> str:
    serialized = json.dumps(
        [0, pubkey_hex, created_at, kind, tags, content],
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(serialized).hexdigest()


# ---------------------------------------------------------------------------
# NostrTransport
# ---------------------------------------------------------------------------

class NostrTransport:
    """
    Nostr relay transport with NIP-44 + ephemeral keypairs.
    Used as Priority-4 fallback in the relay send waterfall.
    """

    def __init__(self, relay_urls: List[str]):
        """
        Args:
            relay_urls: Nostr relay WebSocket URLs from EndpointPreferences.nostr_relays.
        """
        self.relay_urls = relay_urls
        self._running = False
        self._listen_tasks: List[asyncio.Task] = []

    async def start(self) -> None:
        """Mark transport as active. Connections are opened lazily."""
        self._running = True
        logger.info("nostr_transport_started", relay_count=len(self.relay_urls))

    async def stop(self) -> None:
        """Cancel all listeners and mark stopped."""
        self._running = False
        for task in self._listen_tasks:
            task.cancel()
        self._listen_tasks.clear()
        logger.info("nostr_transport_stopped")

    async def send(
        self,
        recipient_ephemeral_pubkey_hex: str,
        encrypted_payload: bytes,
        our_privkey_int: Optional[int] = None,
    ) -> bool:
        """
        Publish a NIP-44 encrypted DM (kind:4) to all configured relays.

        Args:
            recipient_ephemeral_pubkey_hex: 32-byte x-only or 33-byte compressed pubkey hex.
            encrypted_payload: Pre-encrypted message bytes (from messaging.py ECIES).
            our_privkey_int: Our ephemeral private key as integer. Required.

        Returns:
            True if at least one relay accepted the event.
        """
        if not self.relay_urls or our_privkey_int is None:
            return False

        try:
            our_pubkey_bytes = _privkey_to_xonly_pubkey(our_privkey_int)
            our_pubkey_hex = our_pubkey_bytes.hex()

            rec_bytes = bytes.fromhex(recipient_ephemeral_pubkey_hex)
            rec_point = _pubkey_bytes_to_point(rec_bytes)

            # NIP-44 encrypt
            conv_key = _nip44_conversation_key(our_privkey_int, rec_point)
            ciphertext = _nip44_encrypt(encrypted_payload, conv_key)

            # Recipient as 32-byte x-only hex for the "p" tag
            rec_xonly = (rec_bytes if len(rec_bytes) == 32 else rec_bytes[1:]).hex()

            created_at = int(time.time())
            kind = 4
            tags: List[List[str]] = [["p", rec_xonly]]
            content = ciphertext

            eid = _event_id(our_pubkey_hex, created_at, kind, tags, content)
            sig_bytes = _schnorr_sign(bytes.fromhex(eid), our_privkey_int)

            event: Dict[str, Any] = {
                "id": eid,
                "pubkey": our_pubkey_hex,
                "created_at": created_at,
                "kind": kind,
                "tags": tags,
                "content": content,
                "sig": sig_bytes.hex(),
            }
            msg = json.dumps(["EVENT", event])

            results = await asyncio.gather(
                *[self._send_to_relay(url, msg) for url in self.relay_urls],
                return_exceptions=True,
            )
            success = any(r is True for r in results)
            logger.info(
                "nostr_send_done",
                success=success,
                relays=len(self.relay_urls),
                payload_len=len(encrypted_payload),
            )
            return success

        except Exception as exc:
            logger.warning("nostr_send_error", error=str(exc))
            return False

    async def _send_to_relay(self, url: str, msg: str) -> bool:
        """Send a single EVENT to one relay, return True if accepted."""
        try:
            import websockets
            async with websockets.connect(url, open_timeout=6, close_timeout=2) as ws:
                await ws.send(msg)
                try:
                    resp_raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    data = json.loads(resp_raw)
                    # NIP-01: ["OK", <event-id>, <true|false>, <message>]
                    if isinstance(data, list) and data[0] == "OK" and len(data) >= 3:
                        return bool(data[2])
                except Exception:
                    pass
            return False
        except Exception as exc:
            logger.debug("nostr_relay_unavailable", url=url[:30], error=str(exc))
            return False

    async def listen(
        self,
        my_ephemeral_privkey_int: int,
        handler: Callable[[bytes], None],
    ) -> None:
        """
        Subscribe to incoming kind:4 messages on all relays.
        Spawns background tasks that reconnect on disconnect.

        Args:
            my_ephemeral_privkey_int: Our ephemeral private key integer.
            handler: Called with decrypted payload bytes for each received message.
        """
        my_pubkey_hex = _privkey_to_xonly_pubkey(my_ephemeral_privkey_int).hex()
        for url in self.relay_urls:
            task = asyncio.create_task(
                self._listen_relay(url, my_ephemeral_privkey_int, my_pubkey_hex, handler)
            )
            self._listen_tasks.append(task)
        logger.info("nostr_listen_started", relays=len(self.relay_urls), pubkey=my_pubkey_hex[:12])

    async def _listen_relay(
        self,
        url: str,
        my_privkey_int: int,
        my_pubkey_hex: str,
        handler: Callable[[bytes], None],
    ) -> None:
        sub_id = f"qubes-{my_pubkey_hex[:8]}"
        req = json.dumps(["REQ", sub_id, {"kinds": [4], "#p": [my_pubkey_hex]}])

        while self._running:
            try:
                import websockets
                async with websockets.connect(url, open_timeout=6, close_timeout=2) as ws:
                    await ws.send(req)
                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(raw)
                            if (
                                isinstance(data, list)
                                and data[0] == "EVENT"
                                and data[1] == sub_id
                            ):
                                event = data[2]
                                sender_point = _pubkey_bytes_to_point(
                                    bytes.fromhex(event["pubkey"])
                                )
                                conv_key = _nip44_conversation_key(my_privkey_int, sender_point)
                                plaintext = _nip44_decrypt(event["content"], conv_key)
                                handler(plaintext)
                        except Exception as exc:
                            logger.debug("nostr_event_decode_error", error=str(exc))
            except Exception as exc:
                logger.debug("nostr_relay_disconnected", url=url[:30], error=str(exc))
                if self._running:
                    await asyncio.sleep(10)

    @staticmethod
    def derive_session_keypair(
        qube_privkey_bytes: bytes,
        recipient_qube_id: str,
        session_id: str,
    ) -> Tuple[bytes, bytes]:
        """
        Derive an ephemeral (privkey_bytes, xonly_pubkey_bytes) pair for a session.

        HKDF-SHA256 ensures the Nostr keypair is deterministic per session but
        completely unlinkable from the BCH wallet keypair by outside observers.

        Args:
            qube_privkey_bytes: Raw 32-byte Qube secp256k1 private key.
            recipient_qube_id: Recipient's Qube ID (8-char token prefix).
            session_id: Unique session identifier (e.g. conversation_id UUID).

        Returns:
            (ephemeral_privkey_32_bytes, ephemeral_xonly_pubkey_32_bytes)
        """
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives import hashes

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"qubes-nostr-v1",
            info=f"session:{recipient_qube_id}:{session_id}".encode(),
        )
        raw = hkdf.derive(qube_privkey_bytes)

        # Clamp to valid secp256k1 scalar range [1, N-1]
        d = int.from_bytes(raw, "big") % (_N - 1) + 1
        privkey_bytes = d.to_bytes(32, "big")
        pubkey_bytes = _privkey_to_xonly_pubkey(d)
        return privkey_bytes, pubkey_bytes
