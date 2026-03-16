/**
 * Qubes P2P Relay Module
 *
 * Provides peer-to-peer private messaging for Qubes.
 * Zero message content or metadata ever touches the BCH blockchain.
 *
 * Phase 1 — Basic Private Messaging:
 *   - libp2p TCP/WebSocket transport
 *   - Kademlia DHT peer discovery
 *   - Store-and-forward for offline recipients (7-day retention)
 *   - Built-in BitFaced seed relays
 *
 * Phase 2 (TODO): 2-hop onion routing, cover traffic
 * Phase 3 (TODO): Nostr transport fallback with ephemeral keypairs + NIP-44
 * Phase 6 (TODO): BLE, LoRa mesh transport
 *
 * @example
 * ```ts
 * import { createRelayNode, sendMessage, onMessage } from '@qubesai/sdk/relay';
 *
 * const relay = await createRelayNode({ maxConnections: 50 });
 * await relay.start();
 *
 * // Send
 * await sendMessage(relay, {
 *   recipientPubKey: '03a1b2c3...',
 *   senderPrivKey: myPrivKeyBytes,
 *   payload: { type: 'text', content: 'Hello from my Qube' },
 * });
 *
 * // Receive
 * const unsub = onMessage(relay, myPrivKeyBytes, (msg) => {
 *   console.log('Message from', msg.senderPubKey, ':', msg.payload);
 * });
 * ```
 *
 * @module relay
 */

export { createRelayNode, BUILTIN_RELAY_ADDRS } from './relay-node.js';
export { sendMessage } from './send.js';
export { onMessage } from './receive.js';
export type {
  RelayConfig,
  RelayNode,
  RelayNodeStatus,
  RelayPeer,
  RelayMessage,
  SendMessageOptions,
} from './types.js';
