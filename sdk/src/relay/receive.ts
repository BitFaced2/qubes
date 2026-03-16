/**
 * Receive messages via the Qubes P2P relay.
 *
 * onMessage() registers a callback that fires whenever an encrypted message
 * arrives for the given private key. Returns an unsubscribe function.
 */

import type { RelayMessage, RelayNode } from './types.js';

/**
 * Listen for incoming relay messages.
 *
 * @param relay    Running relay node instance.
 * @param privKey  Recipient's secp256k1 private key (raw bytes or hex).
 * @param handler  Called with each decrypted RelayMessage.
 * @returns        Unsubscribe function — call it to stop listening.
 *
 * @example
 * ```ts
 * import { createRelayNode, onMessage } from '@qubesai/sdk/relay';
 *
 * const relay = await createRelayNode();
 * await relay.start();
 *
 * const unsubscribe = onMessage(relay, myPrivKeyBytes, (message) => {
 *   console.log('Received from:', message.senderPubKey);
 *   console.log('Payload:', message.payload);
 * });
 *
 * // Later:
 * unsubscribe();
 * ```
 */
export function onMessage(
  relay: RelayNode,
  privKey: Uint8Array | string,
  handler: (message: RelayMessage) => void,
): () => void {
  void relay; void privKey; void handler;

  // TODO Phase 1: subscribe to DHT topic for our peer ID, decrypt incoming
  // messages with eciesDecrypt(@qubesai/sdk/crypto), call handler for each.
  //
  // For the desktop app this is handled by Tauri get_direct_p2p_messages
  // which polls the Python sidecar → RelayNodeManager.get_pending_messages().

  // Return a no-op unsubscribe for now
  return () => {};
}
