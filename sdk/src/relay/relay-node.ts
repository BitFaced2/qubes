/**
 * Relay Node Factory — Phase 1
 *
 * createRelayNode() is the primary entry point for embedding a Qubes relay
 * node in any application. In the desktop app this is called automatically
 * on startup when relay_enabled = true.
 *
 * The desktop app manages the relay via Tauri commands (init_relay_node,
 * get_relay_status, etc.) which proxy to the Python sidecar. This SDK module
 * provides the same interface for custom frontends and server-side usage.
 *
 * Phase 1: delegates to Python sidecar via IPC.
 * Phase 5: will use the relay bundle natively without Python sidecar.
 */

import type { RelayConfig, RelayNode, RelayNodeStatus, RelayPeer } from './types.js';

/** Built-in BitFaced seed relay multiaddrs (mirrors network/relay_list.py). */
export const BUILTIN_RELAY_ADDRS: string[] = [
  '/ip4/relay-us.qube.cash/tcp/4001/p2p/QmRelayUS',
  '/ip4/relay-us.qube.cash/tcp/443/wss/p2p/QmRelayUS',
  '/ip4/relay-eu.qube.cash/tcp/4001/p2p/QmRelayEU',
  '/ip4/relay-eu.qube.cash/tcp/443/wss/p2p/QmRelayEU',
  '/ip4/relay-as.qube.cash/tcp/4001/p2p/QmRelayAS',
  '/ip4/relay-as.qube.cash/tcp/443/wss/p2p/QmRelayAS',
];

/**
 * Create and return a relay node.
 *
 * The node is NOT started automatically — call node.start() to begin.
 *
 * @example
 * ```ts
 * import { createRelayNode } from '@qubesai/sdk/relay';
 *
 * const relay = await createRelayNode({ maxConnections: 50 });
 * await relay.start();
 * console.log('Relay running:', relay.peerId);
 * ```
 */
export async function createRelayNode(config: RelayConfig = {}): Promise<RelayNode> {
  const bootstrapRelays = config.bootstrapRelays?.length
    ? config.bootstrapRelays
    : BUILTIN_RELAY_ADDRS;

  return new QubesRelayNode(config, bootstrapRelays);
}

// ---------------------------------------------------------------------------
// Internal implementation
// ---------------------------------------------------------------------------

class QubesRelayNode implements RelayNode {
  peerId: string | null = null;
  multiaddr: string | null = null;
  isRunning = false;

  private _config: RelayConfig;
  private _bootstrapRelays: string[];
  private _customPeers: string[] = [];
  private _peerStatus: Map<string, { online: boolean | null; latencyMs: number | null }> = new Map();
  private _pollInterval: ReturnType<typeof setInterval> | null = null;

  constructor(config: RelayConfig, bootstrapRelays: string[]) {
    this._config = config;
    this._bootstrapRelays = bootstrapRelays;
  }

  async start(): Promise<void> {
    if (this.isRunning) return;

    // TODO Phase 5: initialise go-libp2p via WASM or native relay bundle.
    // For now the desktop app uses the Tauri init_relay_node command which
    // routes to the Python sidecar → RelayNodeManager.start().
    this.isRunning = true;

    // Start peer polling every 30 s
    this._pollInterval = setInterval(() => this._pollPeers(), 30_000);
    void this._pollPeers();
  }

  async stop(): Promise<void> {
    if (this._pollInterval) {
      clearInterval(this._pollInterval);
      this._pollInterval = null;
    }
    this.isRunning = false;
    this.peerId = null;
    this.multiaddr = null;
  }

  getStatus(): RelayNodeStatus {
    let onlinePeers = 0;
    for (const s of this._peerStatus.values()) {
      if (s.online) onlinePeers++;
    }
    return {
      running: this.isRunning,
      peerId: this.peerId,
      multiaddr: this.multiaddr,
      peerCount: this._peerStatus.size,
      onlinePeers,
    };
  }

  getPeers(): RelayPeer[] {
    return this._bootstrapRelays.map(addr => ({
      multiaddr: addr,
      label: addr,
      operator: 'BitFaced',
      builtin: true,
      online: this._peerStatus.get(addr)?.online ?? null,
      latencyMs: this._peerStatus.get(addr)?.latencyMs ?? null,
    })).concat(
      this._customPeers.map(addr => ({
        multiaddr: addr,
        label: addr,
        operator: 'custom',
        builtin: false,
        online: this._peerStatus.get(addr)?.online ?? null,
        latencyMs: this._peerStatus.get(addr)?.latencyMs ?? null,
      }))
    );
  }

  async addPeer(multiaddr: string): Promise<boolean> {
    if (this._customPeers.includes(multiaddr)) return false;
    this._customPeers.push(multiaddr);
    return true;
  }

  async removePeer(multiaddr: string): Promise<boolean> {
    const idx = this._customPeers.indexOf(multiaddr);
    if (idx === -1) return false;
    this._customPeers.splice(idx, 1);
    this._peerStatus.delete(multiaddr);
    return true;
  }

  /** Poll all peers for reachability. TODO Phase 1: use actual libp2p ping. */
  private async _pollPeers(): Promise<void> {
    const allPeers = [...this._bootstrapRelays, ...this._customPeers];
    for (const addr of allPeers) {
      // Stub: mark as unknown until real ping is implemented
      if (!this._peerStatus.has(addr)) {
        this._peerStatus.set(addr, { online: null, latencyMs: null });
      }
    }
  }
}
