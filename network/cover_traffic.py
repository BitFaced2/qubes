"""
Cover Traffic Manager — Phase 2 (Scaffold)

Sends dummy encrypted packets at a constant rate to make real message traffic
indistinguishable from background noise. An observer watching any relay node
sees a constant stream of uniform-size packets regardless of real activity.

Inspired by GNUnet's CADET anonymity model:
  "All peers act as routers and use link-encrypted connections with stable
   bandwidth utilization to communicate with each other."

TODO Phase 2: Implement real cover traffic emission.
"""

import asyncio
from typing import Optional

from utils.logging import get_logger

logger = get_logger(__name__)

# Default dummy packet size must match OnionRouter.pad_to_bytes
PACKET_SIZE_BYTES = 512

# Default cover traffic rate: 1 dummy packet per second per active peer connection.
# Configurable via RelayPreferences in a future update.
DEFAULT_RATE_HZ = 1.0


class CoverTrafficManager:
    """
    Emits dummy encrypted packets to camouflage real message timing.

    Phase 2 TODO:
    - Select random peers from relay's known_peers list
    - Encrypt a random payload with the peer's pubkey (indistinguishable from real traffic)
    - Send at DEFAULT_RATE_HZ regardless of real message activity
    - Uniform packet size matching OnionRouter.pad_to_bytes
    """

    def __init__(self, rate_hz: float = DEFAULT_RATE_HZ):
        self.rate_hz = rate_hz
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self, relay_node) -> None:
        """
        Start emitting cover traffic.

        Args:
            relay_node: RelayNodeManager instance to send dummy packets through.

        TODO Phase 2: implement.
        """
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._emit_loop(relay_node))
        logger.info("cover_traffic_started_stub")

    async def stop(self) -> None:
        """Stop cover traffic emission."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("cover_traffic_stopped")

    async def _emit_loop(self, relay_node) -> None:
        """Emit dummy packets at the configured rate. TODO Phase 2."""
        interval = 1.0 / self.rate_hz
        while self._running:
            # TODO Phase 2: pick random peer, send dummy encrypted packet
            logger.debug("cover_traffic_emit_todo")
            await asyncio.sleep(interval)
