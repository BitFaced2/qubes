# Qubes Mobile Relay — Phase 4

Platform-specific relay implementations for mobile devices.

---

## Android

**Full relay node** via gomobile bindings.

### Architecture

```
Android App (Kotlin/Java)
    ↓ JNI
relay.aar  (built with gomobile from go-libp2p)
    ↓
go-libp2p full node (same as desktop)
    ↓
Android Nearby API (BLE + WiFi-Direct)
```

### Build

```bash
# Requires: Go 1.21+, gomobile, Android NDK
cd go/android-relay/
gomobile init
gomobile bind -target=android -o ../../qubes-android/app/libs/relay.aar ./...
```

### Background mode

Uses Android `WorkManager` with `KEEP_ALIVE` constraints. Cover traffic rate
is reduced to 0.1 Hz on battery to preserve battery life.

---

## iOS

**Constrained client** (no background relay daemon due to Apple restrictions).

### Architecture

```
iOS App (Swift)
    ↓ foreground
WebSocket connection to preferred relay (live messaging)
    ↓ background
APNS notification wake → reconnect → drain store-and-forward queue
```

### APNS Setup

1. Generate APNS authentication key at developer.apple.com
2. Place `.p8` key file on BitFaced relay servers
3. Configure `APNSBridge(apns_key_path=..., team_id=..., key_id=...)`

### Privacy

The APNS notification payload is empty. Apple never sees message content
or sender identity. The device token is rotated on each app launch.

---

## Transport priority on mobile

| Priority | Transport | Android | iOS |
|----------|-----------|---------|-----|
| 1 | Local relay daemon | ✅ Full | ❌ Not available |
| 2 | BLE mesh (Nearby) | ✅ Full | ✅ Multipeer |
| 3 | Internet DHT (libp2p) | ✅ Full | ✅ Client only |
| 4 | Nostr fallback | ✅ | ✅ |
| 5 | APNS wake + reconnect | — | ✅ |
