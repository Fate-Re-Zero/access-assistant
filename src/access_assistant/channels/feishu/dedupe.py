from __future__ import annotations

import time


class EventDeduper:
    """In-memory deduper for Feishu event_id / message_id (phase 2)."""

    def __init__(self, max_size: int = 10_000, ttl_seconds: float = 86_400) -> None:
        self._max_size = max(1, max_size)
        self._ttl_seconds = max(60.0, ttl_seconds)
        self._seen: dict[str, float] = {}

    def is_duplicate(self, key: str) -> bool:
        normalized = key.strip()
        if not normalized:
            return False

        now = time.time()
        self._purge_expired(now)

        expires_at = self._seen.get(normalized)
        if expires_at is not None and expires_at > now:
            return True

        self._seen[normalized] = now + self._ttl_seconds
        if len(self._seen) > self._max_size:
            self._purge_oldest()
        return False

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, expires_at in self._seen.items() if expires_at <= now]
        for key in expired:
            self._seen.pop(key, None)

    def _purge_oldest(self) -> None:
        overflow = len(self._seen) - self._max_size
        if overflow <= 0:
            return
        for key, _ in sorted(self._seen.items(), key=lambda item: item[1])[:overflow]:
            self._seen.pop(key, None)
