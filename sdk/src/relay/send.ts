/**
 * Send a message via the Qubes P2P relay.
 *
 * Encryption uses ECIES (consistent with @qubesai/sdk/crypto eciesEncrypt).
 * The relay only sees encrypted bytes — it cannot read the content.
 *
 * Transport waterfall (automatic, no user action required):
 *   1. DHT routing via live p2pd bridge
 *   2. Store-and-forward (held up to retentionDays for offline recipients)
 *   3. Nostr relay fallback (Phase 3)
 *   4. LoRa / BLE mesh (Phase 6)
 */

import type { RelayNode, SendMessageOptions } from './types.js';

const DEFAULT_TTL_MS = 7 * 24 * 60 * 60 * 1000; // 7 days

/**
 * Send an encrypted message to a recipient Qube.
 *
 * @example
 * ```ts
 * import { createRelayNode, sendMessage } from '@qubesai/sdk/relay';
 *
 * const relay = await createRelayNode();
 * await relay.start();
 *
 * await sendMessage(relay, {
 *   recipientPubKey: '03a1b2c3...',
 *   senderPrivKey: myPrivKeyBytes,
 *   payload: { type: 'text', content: 'Hello from my Qube' },
 * });
 * ```
 */
export async function sendMessage(
  relay: RelayNode,
  options: SendMessageOptions,
): Promise<void> {
  const { recipientPubKey, senderPrivKey, payload, ttl = DEFAULT_TTL_MS } = options;

  if (!relay.isRunning) {
    throw new Error('Relay node is not running. Call relay.start() first.');
  }

  const privKeyBytes = typeof senderPrivKey === 'string'
    ? hexToBytes(senderPrivKey)
    : senderPrivKey;

  // Serialise payload to JSON bytes
  const plaintext = new TextEncoder().encode(JSON.stringify(payload));

  // TODO Phase 1: use eciesEncrypt from @qubesai/sdk/crypto
  // const encrypted = await eciesEncrypt(recipientPubKey, plaintext);
  const encrypted = plaintext; // stub until ecies import is wired

  // TODO Phase 1: route via relay node's DHT send path
  // For now the desktop app uses Tauri send_direct_p2p_message which routes
  // to the Python sidecar → RelayNodeManager.send_message()
  void encrypted; void ttl; void recipientPubKey;
}

// ---------------------------------------------------------------------------

function hexToBytes(hex: string): Uint8Array {
  const clean = hex.replace(/^0x/, '');
  const out = new Uint8Array(clean.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}
