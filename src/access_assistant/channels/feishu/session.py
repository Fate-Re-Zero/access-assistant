from __future__ import annotations

import time
from pathlib import Path

from .storage import FeishuStorage


class FeishuSessionStore:
    """Track per-user session versions for /new."""

    def __init__(
        self,
        storage: FeishuStorage | None = None,
        *,
        thread_id_prefix: str = "feishu",
    ) -> None:
        self._storage = storage
        self._versions: dict[tuple[str, str], int] = {}
        self._thread_id_prefix = thread_id_prefix.strip() or "feishu"

    @classmethod
    def from_config(
        cls,
        data_dir: Path | None,
        persistence_enabled: bool,
        *,
        thread_id_prefix: str = "feishu",
    ) -> FeishuSessionStore:
        if persistence_enabled and data_dir is not None:
            db_path = data_dir / "feishu.sqlite"
            return cls(storage=FeishuStorage(db_path), thread_id_prefix=thread_id_prefix)
        return cls(thread_id_prefix=thread_id_prefix)

    def build_thread_id(self, chat_id: str, open_id: str) -> str:
        version = self._get_version(chat_id, open_id)
        base = f"{self._thread_id_prefix}:{chat_id}:{open_id}"
        if version <= 0:
            return base
        return f"{base}:s{version}"

    def reset(self, chat_id: str, open_id: str) -> str:
        key = (chat_id, open_id)
        next_version = self._get_version(chat_id, open_id) + 1
        if self._storage is None:
            self._versions[key] = next_version
        else:
            self._set_version(chat_id, open_id, next_version)
        return self.build_thread_id(chat_id, open_id)

    def _get_version(self, chat_id: str, open_id: str) -> int:
        if self._storage is None:
            return self._versions.get((chat_id, open_id), 0)

        with self._storage._lock, self._storage._connect() as conn:
            row = conn.execute(
                "SELECT version FROM feishu_sessions WHERE chat_id = ? AND open_id = ?",
                (chat_id, open_id),
            ).fetchone()
        if row is None:
            return 0
        return int(row["version"])

    def _set_version(self, chat_id: str, open_id: str, version: int) -> None:
        if self._storage is None:
            self._versions[(chat_id, open_id)] = version
            return

        now = time.time()
        with self._storage._lock, self._storage._connect() as conn:
            conn.execute(
                """
                INSERT INTO feishu_sessions (chat_id, open_id, version, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, open_id) DO UPDATE SET
                    version = excluded.version,
                    updated_at = excluded.updated_at
                """,
                (chat_id, open_id, version, now),
            )
            conn.commit()
