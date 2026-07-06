from __future__ import annotations

from access_assistant.logging_config import DEFAULT_LOG_FORMAT, configure_logging
from access_assistant.tool_trace import preview_value, tool_call_log_enabled


def test_default_log_format_includes_function():
    assert "funcName" in DEFAULT_LOG_FORMAT
    assert "lineno" in DEFAULT_LOG_FORMAT


def test_tool_call_log_enabled_default():
    assert tool_call_log_enabled() in {True, False}


def test_preview_value_truncates_long_text():
    text = preview_value("x" * 2000, max_chars=100)
    assert "truncated" in text
    assert len(text) < 2000


def test_configure_logging_idempotent():
    configure_logging(force=True)
    configure_logging(force=False)


def test_tool_trace_callback_logs(monkeypatch):
    from access_assistant.tool_trace import ToolTraceCallbackHandler, tool_call_log_enabled

    monkeypatch.setenv("TOOL_CALL_LOG", "true")
    assert tool_call_log_enabled()

    handler = ToolTraceCallbackHandler(thread_id="t1", model="gpt-test")
    assert handler.thread_id == "t1"

