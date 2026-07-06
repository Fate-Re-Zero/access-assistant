"""TEMPORARY stage I/O instrumentation.

Enable with STAGE_IO_LOG=true. When no longer needed:
  1. Delete this file
  2. Remove `from .stage_io_log import ...` and all stage_io / log_start / log_end calls in multi_agent.py
  3. Remove STAGE_IO_LOG* from .env
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import os
import time
from typing import Any, Iterator

log = logging.getLogger("access_assistant.stage_io")

_BULK_TEXT_KEYS = frozenset(
    {
        "message",
        "prompt",
        "task_message",
        "response",
        "raw_output",
        "instruction",
    }
)


def enabled() -> bool:
    return os.getenv("STAGE_IO_LOG", "").strip().lower() in {"1", "true", "yes", "on"}


def _summarize_value(key: str, value: Any) -> str:
    if value is None:
        return "-"
    if key == "plan" and isinstance(value, dict):
        tasks = value.get("tasks") or []
        task_refs = ",".join(
            f"{task.get('id')}:{task.get('agent')}"
            for task in tasks
            if isinstance(task, dict)
        )
        reason = str(value.get("reason") or "").strip()
        if len(reason) > 80:
            reason = reason[:80] + "…"
        return f"tasks=[{task_refs or '-'}] reason={reason!r}"
    if key == "tool_calls":
        text = str(value).strip()
        if not text or text == "(none)":
            return "(none)"
        names = [name for name in text.split(",") if name]
        if not names:
            return "(none)"
        if len(names) <= 5 and len(text) <= 120:
            return text
        preview = ",".join(names[:3])
        if len(names) > 3:
            preview += f",…(+{len(names) - 3})"
        return f"{len(names)}tools:{preview}"
    if key in _BULK_TEXT_KEYS:
        return f"{len(str(value))}chars"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
        return f"{len(text)}chars"
    text = str(value).strip()
    if len(text) > 120:
        return f"{len(text)}chars"
    return text


def _summarize_fields(fields: dict[str, Any]) -> str:
    if not fields:
        return "(no fields)"
    return " ".join(f"{key}={_summarize_value(key, value)}" for key, value in fields.items())


def log_start(stage: str, *, thread_id: str = "", **fields: Any) -> float:
    if not enabled():
        return time.perf_counter()
    log.info(
        "[STAGE-IO START] stage=%s thread_id=%s %s",
        stage,
        thread_id or "-",
        _summarize_fields(fields),
    )
    return time.perf_counter()


def log_end(
    stage: str,
    started_at: float,
    *,
    thread_id: str = "",
    error: str = "",
    **fields: Any,
) -> None:
    if not enabled():
        return
    duration_ms = (time.perf_counter() - started_at) * 1000
    status = "ERROR" if error else "OK"
    payload = dict(fields)
    if error:
        payload["error"] = error
    log.info(
        "[STAGE-IO END] stage=%s thread_id=%s status=%s duration_ms=%.1f %s",
        stage,
        thread_id or "-",
        status,
        duration_ms,
        _summarize_fields(payload),
    )


@contextmanager
def stage_io(stage: str, *, thread_id: str = "", **inputs: Any) -> Iterator[dict[str, Any]]:
    holder: dict[str, Any] = {}
    started = log_start(stage, thread_id=thread_id, **inputs)
    try:
        yield holder
    except Exception as exc:
        log_end(stage, started, thread_id=thread_id, error=str(exc))
        raise
    else:
        log_end(stage, started, thread_id=thread_id, **holder)
