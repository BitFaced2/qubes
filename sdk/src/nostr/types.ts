/**
 * Nostr transport type definitions — Phase 3 (Scaffold)
 */

/** A Nostr relay WebSocket endpoint. */
export interface NostrRelay {
  url: string;
  connected: boolean;
}

/** Configuration for the Nostr transport layer. */
export interface NostrConfig {
  /** Nostr relay WebSocket URLs. Defaults to EndpointPreferences.nostr_relays. */
  relayUrls?: string[];
}

/** An ephemeral keypair for a single Nostr session. Not linked to BCH identity. */
export interface NostrSessionKeypair {
  privateKey: Uint8Array;
  publicKey: Uint8Array;
}
