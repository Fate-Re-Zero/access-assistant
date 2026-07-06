from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class FeishuStorage:
    """SQLite storage for Feishu session versions and audit logs."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS feishu_sessions (
                    chat_id TEXT NOT NULL,
                    open_id TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (chat_id, open_id)
                );

                CREATE TABLE IF NOT EXISTS feishu_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    direction TEXT NOT NULL,
                    event_id TEXT,
                    message_id TEXT,
                    chat_id TEXT NOT NULL,
                    open_id TEXT NOT NULL,
                    thread_id TEXT,
                    user_name TEXT,
                    user_email TEXT,
                    content TEXT,
                    status TEXT NOT NULL,
                    meta_json TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_feishu_audit_created_at
                    ON feishu_audit(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_feishu_audit_open_id
                    ON feishu_audit(open_id, created_at DESC);
                """
            )
            conn.commit()
