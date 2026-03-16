/**
 * Type definitions for the Qubes P2P relay module.
 */

/** Configuration for creating a relay node. */
export interface RelayConfig {
  /** Bootstrap relay multiaddrs. Uses BUILTIN_RELAYS if empty. */
  bootstrapRelays?: string[];
  /** Addresses to listen on. Defaults to auto-assigned TCP + WebSocket. */
  listenAddrs?: string[];
  /** Max number of peer connections. Default: 50. */
  maxConnections?: number;
  /** Store-and-forward retention in days. Default: 7. */
  retentionDays?: number;
  /**
   * Path to p2pd binary.
   * Undefined = use the bundled binary inside the Qubes app data directory.
   */
  p2pdBinaryPath?: string;
}

/** A running relay node instance. */
export interface RelayNode {
  /** libp2p peer ID of this node. */
  peerId: string | null;
  /** Primary multiaddr this node is listening on. */
  multiaddr: string | null;
  /** Whether the node is currently running. */
  isRunning: boolean;
  /** Start the relay node. */
  start(): Promise<void>;
  /** Stop the relay node. */
  stop(): Promise<void>;
  /** Get current status. */
  getStatus(): RelayNodeStatus;
  /** Get peer list with live reachability. */
  getPeers(): RelayPeer[];
  /** Add a custom peer. */
  addPeer(multiaddr: string): Promise<boolean>;
  /** Remove a custom peer. */
  removePeer(multiaddr: string): Promise<boolean>;
}

/** Live status of a relay node. */
export interface RelayNodeStatus {
  running: boolean;
  peerId: string | null;
  multiaddr: string | null;
  peerCount: number;
  onlinePeers: number;
}

/** A known relay peer entry with live reachability. */
export interface RelayPeer {
  multiaddr: string;
  label: string;
  operator: string;
  builtin: boolean;
  /** true = reachable, false = unreachable, null = not yet checked */
  online: boolean | null;
  latencyMs: number | null;
}

/** An encrypted message delivered via the relay. */
export interface RelayMessage {
  /** Unique message ID (random UUID). */
  id: string;
  /** Sender's Qube public key (compressed secp256k1 hex). */
  senderPubKey: string;
  /** Recipient's Qube public key (compressed secp256k1 hex). */
  recipientPubKey: string;
  /** Decrypted message payload. */
  payload: unknown;
  /** Unix timestamp (ms) when the message was sent. */
  timestamp: number;
  /** Unix timestamp (ms) after which the relay may discard the message. */
  ttl: number;
}

/** Options for sending a message via relay. */
export interface SendMessageOptions {
  /** Recipient's secp256k1 public key (compressed hex). */
  recipientPubKey: string;
  /** Sender's secp256k1 private key (raw bytes or hex). */
  senderPrivKey: Uint8Array | string;
  /** Arbitrary message payload (will be JSON-serialised before encryption). */
  payload: unknown;
  /**
   * Time-to-live in milliseconds from now.
   * Default: 7 days (604800000 ms).
   */
  ttl?: number;
}
