"""
subscribers.py — Track users who have subscribed to the bot.

Anyone who runs /start is added as a subscriber.
Scheduled jobs broadcast to all subscribers.
Command-triggered jobs reply only to the caller.
"""

import json
import logging
import os
from typing import Set

log = logging.getLogger(__name__)

SUBSCRIBERS_FILE = "subscribers.json"


class SubscriberManager:

    def __init__(self, path: str = SUBSCRIBERS_FILE):
        self.path = path
        self._subs: Set[int] = set()
        self._load()

    # ── Persistence ───────────────────────────────────────────────

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r") as f:
                data = json.load(f)
            self._subs = set(int(x) for x in data.get("chat_ids", []))
            log.info(f"Loaded {len(self._subs)} subscribers")
        except Exception as e:
            log.warning(f"Subscriber load failed: {e}")

    def _save(self):
        try:
            with open(self.path, "w") as f:
                json.dump({"chat_ids": sorted(self._subs)}, f, indent=2)
        except Exception as e:
            log.warning(f"Subscriber save failed: {e}")

    # ── Public API ────────────────────────────────────────────────

    def add(self, chat_id: int) -> bool:
        """Add subscriber. Returns True if newly added."""
        chat_id = int(chat_id)
        if chat_id in self._subs:
            return False
        self._subs.add(chat_id)
        self._save()
        log.info(f"➕ Subscriber added: {chat_id} (total: {len(self._subs)})")
        return True

    def remove(self, chat_id: int) -> bool:
        """Remove subscriber. Returns True if was present."""
        chat_id = int(chat_id)
        if chat_id not in self._subs:
            return False
        self._subs.discard(chat_id)
        self._save()
        log.info(f"➖ Subscriber removed: {chat_id} (total: {len(self._subs)})")
        return True

    def all(self) -> list:
        """Return all subscriber chat_ids."""
        return list(self._subs)

    def count(self) -> int:
        return len(self._subs)

    def has(self, chat_id: int) -> bool:
        return int(chat_id) in self._subs
