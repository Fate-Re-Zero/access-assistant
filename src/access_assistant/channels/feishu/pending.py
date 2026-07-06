from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class PendingFile:
    file_name: str
    file_content: str
    truncated: bool
    source_message_id: str


@dataclass(frozen=True)
class PendingQuestion:
    text: str


@dataclass
class _PendingEntry:
    file: PendingFile | None = None
    question: PendingQuestion | None = None
    updated_at: float = 0.0


class FeishuPendingStore:
    """In-memory bidirectional pending state for file + question pairing."""

    def __init__(self, *, ttl_seconds: float = 600.0, max_size: int = 5000) -> None:
        self._ttl_seconds = max(ttl_seconds, 0.001)
        self._max_size = max(max_size, 1)
        self._entries: dict[tuple[str, str], _PendingEntry] = {}

    def get_file(self, chat_id: str, open_id: str) -> PendingFile | None:
        entry = self._get_valid_entry(chat_id, open_id)
        if entry is None or entry.file is None:
            return None
        return entry.file

    def get_question(self, chat_id: str, open_id: str) -> PendingQuestion | None:
        entry = self._get_valid_entry(chat_id, open_id)
        if entry is None or entry.question is None:
            return None
        return entry.question

    def has_pending(self, chat_id: str, open_id: str) -> bool:
        entry = self._get_valid_entry(chat_id, open_id)
        if entry is None:
            return False
        return entry.file is not None or entry.question is not None

    def set_file(self, chat_id: str, open_id: str, pending_file: PendingFile) -> None:
        self._purge_expired()
        key = (chat_id, open_id)
        entry = self._entries.get(key) or _PendingEntry()
        entry.file = pending_file
        entry.updated_at = time.monotonic()
        self._entries[key] = entry
        self._enforce_max_size()

    def set_question(self, chat_id: str, open_id: str, pending_question: PendingQuestion) -> None:
        self._purge_expired()
        key = (chat_id, open_id)
        entry = self._entries.get(key) or _PendingEntry()
        entry.question = pending_question
        entry.updated_at = time.monotonic()
        self._entries[key] = entry
        self._enforce_max_size()

    def clear(self, chat_id: str, open_id: str) -> None:
        self._entries.pop((chat_id, open_id), None)

    def _get_valid_entry(self, chat_id: str, open_id: str) -> _PendingEntry | None:
        key = (chat_id, open_id)
        entry = self._entries.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry.updated_at > self._ttl_seconds:
            self._entries.pop(key, None)
            return None
        return entry

    def _purge_expired(self) -> None:
        now = time.monotonic()
        expired = [
            key
            for key, entry in self._entries.items()
            if now - entry.updated_at > self._ttl_seconds
        ]
        for key in expired:
            self._entries.pop(key, None)

    def _enforce_max_size(self) -> None:
        overflow = len(self._entries) - self._max_size
        if overflow <= 0:
            return
        oldest = sorted(self._entries.items(), key=lambda item: item[1].updated_at)
        for key, _entry in oldest[:overflow]:
            self._entries.pop(key, None)
