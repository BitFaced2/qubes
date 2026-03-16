"""
Store-and-Forward Queue — Phase 1

JSON file-backed message queue that holds encrypted messages for offline
recipients. Messages are purged after delivery or when TTL expires (default 7 days).

No new dependencies — pure stdlib JSON + file I/O.
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

from utils.logging import get_logger

logger = get_logger(__name__)

_QUEUE_FILE = "relay_queue.json"


class StoreForwardQueue:
    """
    Encrypted message queue for offline delivery.

    Storage format (relay_queue.json):
    {
      "recipient_qube_id": [
        {"payload_hex": "...", "queued_at": 1234567890, "ttl": 1234567890},
        ...
      ]
    }
    """

    def __init__(self, queue_dir: Path, retention_days: int = 7):
        self.queue_file = Path(queue_dir) / _QUEUE_FILE
        self.retention_seconds = retention_days * 86400
        self._queue: Dict[str, List[Dict[str, Any]]] = {}
        self._load()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def enqueue(self, recipient_id: str, encrypted_payload: bytes, ttl_timestamp: int) -> None:
        """Hold an encrypted message until the recipient connects."""
        if recipient_id not in self._queue:
            self._queue[recipient_id] = []

        self._queue[recipient_id].append({
            "payload_hex": encrypted_payload.hex(),
            "queued_at": int(time.time()),
            "ttl": ttl_timestamp,
        })
        self._save()
        logger.info("sfq_enqueued", recipient=recipient_id, queue_depth=len(self._queue[recipient_id]))

    def dequeue(self, recipient_id: str) -> List[Dict[str, Any]]:
        """
        Return all pending messages for a recipient and remove them from the queue.
        Returns list of dicts with keys: payload (bytes), queued_at (int).
        """
        if recipient_id not in self._queue:
            return []

        messages = self._queue.pop(recipient_id)
        self._save()

        now = int(time.time())
        result = []
        for msg in messages:
            if msg["ttl"] >= now:
                result.append({
                    "payload": bytes.fromhex(msg["payload_hex"]),
                    "queued_at": msg["queued_at"],
                })
        logger.info("sfq_dequeued", recipient=recipient_id, count=len(result))
        return result

    def purge_expired(self) -> int:
        """Delete all messages whose TTL has passed. Returns number of messages purged."""
        now = int(time.time())
        total_purged = 0

        for recipient_id in list(self._queue.keys()):
            before = len(self._queue[recipient_id])
            self._queue[recipient_id] = [m for m in self._queue[recipient_id] if m["ttl"] >= now]
            purged = before - len(self._queue[recipient_id])
            total_purged += purged
            if not self._queue[recipient_id]:
                del self._queue[recipient_id]

        if total_purged:
            self._save()
            logger.info("sfq_purged", count=total_purged)

        return total_purged

    def queue_depth(self, recipient_id: str) -> int:
        """Return number of pending messages for a recipient."""
        return len(self._queue.get(recipient_id, []))

    def total_queued(self) -> int:
        """Return total messages across all recipients."""
        return sum(len(msgs) for msgs in self._queue.values())

    # -------------------------------------------------------------------------
    # Internal persistence
    # -------------------------------------------------------------------------

    def _load(self) -> None:
        if not self.queue_file.exists():
            self._queue = {}
            return
        try:
            with open(self.queue_file, "r", encoding="utf-8") as f:
                self._queue = json.load(f)
        except (json.JSONDecodeError, OSError):
            self._queue = {}

    def _save(self) -> None:
        try:
            with open(self.queue_file, "w", encoding="utf-8") as f:
                json.dump(self._queue, f)
        except OSError as exc:
            logger.warning("sfq_save_failed", error=str(exc))
