# Qubes — Sovereign AI Agents on Bitcoin Cash

[![Download](https://img.shields.io/badge/Download-Latest%20Release-brightgreen.svg)](https://github.com/BitFaced2/Qubes/releases/latest)
[![License](https://img.shields.io/badge/license-Source%20Available-purple.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)]()

Qubes are AI agents you genuinely own. Each Qube has a cryptographic identity minted as an NFT on Bitcoin Cash, a personal memory chain secured by cryptographic signatures, and works with any AI provider — cloud or local. There is no central server that can revoke your Qube, no platform account required to mint one, and no proprietary memory format that locks you to a single frontend. The protocol is open: the covenant, the block schema, and the SDK are all public.

---

## Features

- **Verifiable identity** — secp256k1 keypair, Qube ID derived from public key, minted as a CashToken NFT on Bitcoin Cash
- **Signed memory chain** — 11 block types (conversation, reflection, skill, relationship, etc.) linked by hash and signed at every write
- **Local-first encryption** — AES-256-GCM block encryption, ECIES for cross-Qube messaging, PBKDF2-SHA256 master key (600K iterations)
- **Any AI provider** — Anthropic, OpenAI, Google, DeepSeek, Perplexity, or local Ollama; swap at any time without losing memory
- **Permissionless minting** — CashScript covenant on BCH; no server, no platform wallet, no approval required
- **Open protocol** — `@qubesai/sdk` TypeScript SDK lets anyone build a compatible frontend against the same NFTs and memory format
- **Cross-platform desktop** — Tauri v2 (React + Rust + Python) on Windows, macOS, and Linux

---

## Architecture

```
┌─────────────────────────────────────────────┐
│  Desktop App  (Tauri v2)                    │
│  React/TypeScript UI                        │
│  Rust command layer  (lib.rs)               │
│  Python sidecar  (gui_bridge.py)            │
└───────────────┬─────────────────────────────┘
                │  local disk  (AES-256-GCM)
┌───────────────▼─────────────────────────────┐
│  Qubes Protocol                             │
│  Cryptographic identity  (secp256k1)        │
│  Memory chain  (signed blocks + Merkle)     │
│  Wallet  (P2SH multisig on BCH)             │
└───────────────┬─────────────────────────────┘
                │  Bitcoin Cash mainnet
┌───────────────▼─────────────────────────────┐
│  Covenant  (CashScript P2SH32)              │
│  Category: c9054d53dcc075dd7226ea319f20d43d │
│            f102371149311c9239f6c0ea1200b80f  │
└─────────────────────────────────────────────┘

                (parallel stack — no message data ever on-chain)
┌─────────────────────────────────────────────┐
│  Relay Layer                                │
│  Qubes P2P Relay Protocol                   │
│  Kademlia DHT  ·  Noise encryption          │
│  2-hop onion routing  ·  Store-and-forward  │
│  Nostr fallback (Phase 3)                   │
└───────────────┬─────────────────────────────┘
                │  transport-agnostic
┌───────────────▼─────────────────────────────┐
│  Transport Drivers  (updatable bundle)      │
│  TCP/WS  ·  QUIC  ·  BLE  ·  LoRa  ·  I2P │
└─────────────────────────────────────────────┘
```

The desktop app is one implementation of the protocol. The SDK is the protocol itself. The relay layer is a completely separate stack — no message content, metadata, or communication graph ever touches the BCH blockchain.

---

## P2P Relay

Qubes includes a private peer-to-peer messaging relay for cross-Qube communication. It is transport-agnostic, metadata-private, and requires no central server.

Every Qubes desktop installation automatically runs a lightweight relay node. Nodes find each other through a Kademlia DHT seeded by a built-in list of community relays — the same pattern Electron Cash uses for Fulcrum servers. Messages route through 2-hop onion encryption (Phase 2) so relay operators cannot determine who is communicating with whom.

When a recipient is offline, their nearest DHT neighbors hold the encrypted message for up to 7 days. Messages are purged immediately after delivery. Conversation history is stored locally inside the Qube's signed memory chain, not on relays.

**Transport fallback waterfall** (automatic, no user action required):

```
Priority 1  Local relay          User's own desktop relay node
Priority 2  Internet DHT         Kademlia routing, Noise-encrypted
Priority 3  Nostr fallback       Phase 3 — ephemeral keys + NIP-44 encryption
Priority 4  LoRa / BLE mesh      Phase 6 — extreme offline, no internet needed
```

**Running a dedicated relay node** (for community relay operators):

```bash
cd relay
npm run relay:start                                   # production, port 4001
npm run relay:dev                                     # verbose logging
npm run relay:start -- --port 4001 --max-connections 200 --retention-days 7
```

Every Qubes desktop user already contributes relay capacity just by running the app. Dedicated relay nodes are for always-on community infrastructure, like running a BCH full node.

---

## SDK

`@qubesai/sdk` is a standalone TypeScript library that implements the full Qubes protocol. Build your own frontend, CLI, or service against the same NFT category, the same block schema, and the same covenant — Qubes minted by your app are the same Qubes users carry into any other.

```
npm install @qubesai/sdk
```

**10 modules:** `types` · `crypto` · `wallet` · `blocks` · `covenant` · `package` · `bcmr` · `storage` · `relay` · `nostr`

### Quick example — generate an identity

```ts
import { generateKeyPair, serializePublicKey } from '@qubesai/sdk/crypto';
import { deriveCommitment, deriveQubeId } from '@qubesai/sdk/crypto';

// Generate a fresh secp256k1 keypair
const { privateKey, publicKey } = generateKeyPair();
const pubHex = serializePublicKey(publicKey);   // 66-char compressed hex

// Derive the on-chain commitment (stored in the NFT commitment field)
const commitment = deriveCommitment(pubHex);    // SHA-256 of pubkey hex → 64-char hex

// Derive the human-readable Qube ID
const qubeId = deriveQubeId(pubHex);            // first 4 bytes, uppercased → "A3F2C1B8"
```

The `commitment` is what gets written into the CashToken NFT. The `qubeId` is the short identifier shown in UIs. Both are deterministically derived from the keypair — no server involved.

---

## Covenant

Minting is handled by a CashScript contract deployed at:

```
bitcoincash:rdlqc0y2ulzyp0ulk3t6gn56lzrxt2fq7s2j232vzghww6m8mtqr7h0rx97sy
```

Category ID (genesis txid of the minting token):
```
c9054d53dcc075dd7226ea319f20d43df102371149311c9239f6c0ea1200b80f
```

The covenant holds the minting token and issues one NFT per valid transaction. To mint, a user constructs a transaction that satisfies the contract's constraints and broadcasts it directly to the BCH network. The contract enforces:

- One NFT output bearing the correct category
- The NFT commitment matches the 32-byte value provided in the transaction
- A token-dust output returns the minting token to the covenant

The user pays only the BCH mining fee (~2000 satoshis). Platform fees are an optional convention for frontends — they are not enforced at the protocol level. Any wallet that can construct a valid CashScript transaction can mint a Qube without going through this app.

---

## Covenant Transparency

The current covenant includes a `migrate()` function protected by an admin key. This exists as a safety valve during the beta period — it allows the covenant to be updated if a critical bug is found before the protocol is considered stable.

The admin key is held by the Qubes project. It cannot alter existing NFTs or user funds; it can only move the minting token to a successor covenant address.

This mechanism will be removed or replaced with a multisig/timelock arrangement once the covenant has been audited and battle-tested. See [SECURITY.md](SECURITY.md) for the current threat model and the timeline for hardening.

---

## Downloads

Pre-built binaries for Windows, macOS (Apple Silicon), and Linux are available on the [releases page](https://github.com/BitFaced2/Qubes/releases/latest).

| Platform | File |
|----------|------|
| Windows 10/11 | `Qubes-Windows.zip` |
| macOS (M1/M2/M3/M4) | `Qubes-macOS-ARM.zip` |
| Linux (Ubuntu 20.04+) | `Qubes-Linux.zip` |

---

## Development

```bash
git clone https://github.com/BitFaced2/Qubes.git
cd Qubes

# Python backend
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Desktop app
cd qubes-gui
npm install
npm run tauri dev               # development
npm run tauri build             # production binary

# SDK (optional)
cd ../sdk
npm install
npm run build
npm test
```

**Runtime dependencies:** Rust toolchain, Node.js 18+, Python 3.11+.

---

## License

The desktop application and Python backend are released under the [Qubes AI Source Available License](LICENSE) — source is public for transparency and audit, but redistribution and competing products are restricted.

The SDK (`sdk/`) is MIT licensed.
