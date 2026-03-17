"""
Android Relay Node — Phase 4 (Scaffold)

Full relay node for Android via gomobile bindings.

Architecture:
  - Go library built with gomobile: go-libp2p full node
  - Python sidecar calls into the Go library via JNI bridge
  - Android Nearby API for BLE + WiFi-Direct discovery
  - Background service using WorkManager for keepalive

gomobile build:
  cd go/android-relay
  gomobile bind -target=android -o relay.aar ./...

The .aar is bundled in the Android APK and exposed via JNI to the Python sidecar.

Key binding surface (mirrors RelayNodeManager interface):

  func Start(port int, maxConns int) string       # returns peer_id
  func Stop()
  func GetStatus() string                         # JSON
  func AddPeer(multiaddr string) bool
  func RemovePeer(multiaddr string) bool
  func SendMessage(recipientId string, payload []byte, ttlDays int) bool
  func GetPendingMessages(qubeId string) string   # JSON array

TODO Phase 4:
  1. Create go/android-relay/ Go module with gomobile bindings
  2. Add JNI bridge in qubes-android/app/src/main/jni/
  3. Call Start() from Android background service
  4. Wire Android Nearby API to ble_transport
  5. Implement battery-aware background mode (reduce cover traffic rate on battery)
"""

from utils.logging import get_logger

logger = get_logger(__name__)


class AndroidRelayNode:
    """
    Stub for the Android gomobile relay node binding.

    On Android, this class is replaced by a JNI call into the compiled .aar.
    On desktop, the standard RelayNodeManager is used instead.
    """

    def __init__(self):
        raise NotImplementedError(
            "AndroidRelayNode requires the gomobile .aar build. "
            "Use RelayNodeManager on desktop."
        )
