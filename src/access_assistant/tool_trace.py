"""Structured logging for tool invocations (local tools + MCP)."""

from __future__ import annotations

import contextvars
import json
import logging
import os
import time
from typing import Any, Optional
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

log = logging.getLogger(__name__)

_tool_trace_context: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "tool_trace_context",
    default={},
)


def set_tool_trace_context(**fields: str) -> None:
    current = dict(_tool_trace_context.get())
    current.update({key: str(value) for key, value in fields.items() if value})
    _tool_trace_context.set(current)


def get_tool_trace_context() -> dict[str, str]:
    return dict(_tool_trace_context.get())


def is_mcp_tool_name(tool_name: str) -> bool:
    return "-mcp-server_" in str(tool_name or "")


def tool_call_log_enabled() -> bool:
    return os.getenv("TOOL_CALL_LOG", "true").strip().lower() in {"1", "true", "yes", "on"}


def _max_preview_chars() -> int:
    try:
        return max(80, int(os.getenv("TOOL_CALL_LOG_MAX_CHARS", "800")))
    except ValueError:
        return 800


def _mcp_result_preview_chars() -> int:
    try:
        return max(40, int(os.getenv("MCP_RESULT_LOG_MAX_CHARS", "160")))
    except ValueError:
        return 160


def preview_value(value: Any, *, max_chars: int | None = None) -> str:
    limit = max_chars if max_chars is not None else _max_preview_chars()
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        try:
            text = json.dumps(value, ensure_ascii=False)
        except TypeError:
            text = str(value)
    else:
        text = str(value)
    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…(truncated, total {len(text)} chars)"


def preview_mcp_result(result: Any) -> str:
    """Summarize MCP output without dumping full cipher payloads."""
    cipher_len: int | None = None
    if isinstance(result, tuple) and result:
        result = result[0]
    if isinstance(result, list):
        for item in result:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "")
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict) and isinstance(payload.get("result"), str):
                cipher_len = len(payload["result"])
                break
            if text.startswith("{") and "result" in text:
                cipher_len = len(text)
                break
    if cipher_len is not None:
        return f"encrypted_result={cipher_len}chars"
    return preview_value(result, max_chars=_mcp_result_preview_chars())


def log_tool_call(tool_name: str, **fields: Any) -> float:
    if not tool_call_log_enabled():
        return time.perf_counter()
    parts = [f"tool={tool_name}"]
    if is_mcp_tool_name(tool_name):
        ordered_keys = ["source", "thread_id", "model", "mcp_args"]
        for key in ordered_keys:
            if key in fields:
                parts.append(f"{key}={preview_value(fields.pop(key))}")
    for key, value in fields.items():
        parts.append(f"{key}={preview_value(value)}")
    log.info("[TOOL CALL] %s", " ".join(parts))
    return time.perf_counter()


def log_mcp_tool_call(tool_name: str, mcp_args: dict[str, Any]) -> float:
    return log_tool_call(
        tool_name,
        source="mcp",
        mcp_args=mcp_args,
        **get_tool_trace_context(),
    )


def log_tool_result(
    tool_name: str,
    started_at: float,
    *,
    success: bool,
    preview: str = "",
    error: str = "",
    **fields: Any,
) -> None:
    if not tool_call_log_enabled():
        return
    duration_ms = (time.perf_counter() - started_at) * 1000
    status = "OK" if success else "FAIL"
    parts = [
        f"tool={tool_name}",
        f"status={status}",
        f"duration_ms={duration_ms:.1f}",
    ]
    if is_mcp_tool_name(tool_name):
        ordered_keys = ["source", "thread_id", "model"]
        for key in ordered_keys:
            if key in fields:
                parts.append(f"{key}={preview_value(fields.pop(key))}")
    for key, value in fields.items():
        parts.append(f"{key}={preview_value(value)}")
    if preview:
        parts.append(f"preview={preview_value(preview)}")
    if error:
        parts.append(f"error={preview_value(error)}")
    if success:
        log.info("[TOOL RESULT] %s", " ".join(parts))
    else:
        log.warning("[TOOL RESULT] %s", " ".join(parts))


def log_mcp_tool_result(
    tool_name: str,
    started_at: float,
    *,
    success: bool,
    result: Any = None,
    error: str = "",
) -> None:
    preview = preview_mcp_result(result) if success else ""
    log_tool_result(
        tool_name,
        started_at,
        success=success,
        preview=preview,
        error=error,
        source="mcp",
        **get_tool_trace_context(),
    )


class ToolTraceCallbackHandler(BaseCallbackHandler):
    """LangChain callback: logs tool/MCP start and end for invoke + stream paths."""

    def __init__(self, *, thread_id: str = "", model: str = "") -> None:
        self.thread_id = thread_id
        self.model = model
        self.tool_calls: list[str] = []
        self._pending: dict[str, tuple[str, float]] = {}

    def reset(self) -> None:
        self.tool_calls = []
        self._pending = {}

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
        inputs: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        name = str(serialized.get("name") or "unknown")
        if is_mcp_tool_name(name):
            self.tool_calls.append(name)
            return None
        self.tool_calls.append(name)
        key = str(run_id)
        args = inputs if inputs is not None else input_str
        started = log_tool_call(
            name,
            thread_id=self.thread_id,
            model=self.model,
            args=args,
        )
        self._pending[key] = (name, started)

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> Any:
        key = str(run_id)
        name, started = self._pending.pop(key, ("unknown", time.perf_counter()))
        if is_mcp_tool_name(name):
            return None
        log_tool_result(
            name,
            started,
            success=True,
            preview=preview_value(output),
            thread_id=self.thread_id,
            model=self.model,
        )

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> Any:
        key = str(run_id)
        name, started = self._pending.pop(key, ("unknown", time.perf_counter()))
        if is_mcp_tool_name(name):
            return None
        log_tool_result(
            name,
            started,
            success=False,
            error=str(error),
            thread_id=self.thread_id,
            model=self.model,
        )
