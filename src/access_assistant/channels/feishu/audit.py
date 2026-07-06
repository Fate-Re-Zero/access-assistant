from __future__ import annotations

import json
import re
import time
from typing import Any

from .storage import FeishuStorage

_SENSITIVE_PATTERNS = (
    (re.compile(r"\b1[3-9]\d{9}\b"), "[手机号已脱敏]"),
    (re.compile(r"\b\d{15,19}\b"), "[数字已脱敏]"),
)


def redact_audit_content(text: str, max_length: int) -> str:
    normalized = (text or "").strip()
    if not normalized:
        return ""

    for pattern, replacement in _SENSITIVE_PATTERNS:
        normalized = pattern.sub(replacement, normalized)

    if len(normalized) <= max_length:
        return normalized
    return normalized[: max(0, max_length - 1)] + "…"


class FeishuAuditLogger:
    def __init__(self, storage: FeishuStorage, max_content_length: int = 2000) -> None:
        self._storage = storage
        self._max_content_length = max(200, max_content_length)

    def log(
        self,
        *,
        direction: str,
        chat_id: str,
        open_id: str,
        status: str,
        content: str = "",
        event_id: str | None = None,
        message_id: str | None = None,
        thread_id: str | None = None,
        user_name: str | None = None,
        user_email: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        payload = redact_audit_content(content, self._max_content_length)
        meta_json = json.dumps(meta or {}, ensure_ascii=False)
        with self._storage._lock, self._storage._connect() as conn:
            conn.execute(
                """
                INSERT INTO feishu_audit (
                    created_at, direction, event_id, message_id, chat_id, open_id,
                    thread_id, user_name, user_email, content, status, meta_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    direction,
                    event_id,
                    message_id,
                    chat_id,
                    open_id,
                    thread_id,
                    user_name,
                    user_email,
                    payload,
                    status,
                    meta_json,
                ),
            )
            conn.commit()
