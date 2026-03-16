/**
 * Qubes Nostr Transport Module — Phase 3 (Scaffold)
 *
 * Uses public Nostr relays as a Priority-4 transport fallback when the DHT
 * is unreachable. Implements maximum-privacy messaging (BitChat-style):
 *
 * - Ephemeral keypair per session — NOT linked to the user's BCH wallet
 * - NIP-44 encrypted DMs (ChaCha20-Poly1305 + secp256k1 ECDH)
 * - Broadcast to all configured Nostr relays simultaneously
 * - Relay list from EndpointPreferences.nostr_relays (Settings → Endpoints)
 *
 * TODO Phase 3: implement all functions below.
 *
 * @module nostr
 */

export type { NostrConfig, NostrRelay, NostrSessionKeypair } from './types.js';

/**
 * Derive an ephemeral session keypair unlinkable to BCH identity.
 * TODO Phase 3: implement secp256k1 HKDF derivation.
 */
export function deriveSessionKeypair(
  _qubePrivKey: Uint8Array,
  _recipientQubeId: string,
  _sessionId: string,
): { privateKey: Uint8Array; publicKey: Uint8Array } {
  throw new Error('Nostr transport not yet implemented (Phase 3)');
}

/**
 * Send a NIP-44 encrypted DM via all configured Nostr relays.
 * TODO Phase 3: implement WebSocket + NIP-44 encryption.
 */
export async function nostrSend(
  _relayUrls: string[],
  _recipientEphemeralPubKey: string,
  _encryptedPayload: Uint8Array,
): Promise<void> {
  throw new Error('Nostr transport not yet implemented (Phase 3)');
}

/**
 * Subscribe to incoming Nostr DMs for an ephemeral inbox key.
 * TODO Phase 3: implement NIP-01 REQ subscription + NIP-44 decryption.
 */
export function nostrListen(
  _relayUrls: string[],
  _ephemeralPrivKey: Uint8Array,
  _handler: (payload: Uint8Array) => void,
): () => void {
  throw new Error('Nostr transport not yet implemented (Phase 3)');
}
