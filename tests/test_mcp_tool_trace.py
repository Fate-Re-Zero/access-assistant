from __future__ import annotations

import logging

from access_assistant.tool_trace import (
    log_mcp_tool_call,
    log_mcp_tool_result,
    preview_mcp_result,
    set_tool_trace_context,
)


def test_log_mcp_tool_call_includes_mcp_args(caplog, monkeypatch):
    monkeypatch.setenv("TOOL_CALL_LOG", "true")
    caplog.set_level(logging.INFO, logger="access_assistant.tool_trace")
    set_tool_trace_context(thread_id="thread-auth_1", model="gpt-test")

    log_mcp_tool_call(
        "auth-mcp-server_sqg_query_account_info",
        {"inputAccount": "3599948273", "token": ""},
    )

    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 1
    assert "auth-mcp-server_sqg_query_account_info" in messages[0]
    assert "mcp_args=" in messages[0]
    assert "3599948273" in messages[0]
    assert "thread_id=thread-auth_1" in messages[0]
    assert "model=gpt-test" in messages[0]


def test_preview_mcp_result_hides_cipher():
    result = (
        [
            {
                "type": "text",
                "text": '{"result":"' + ("A" * 500) + '"}',
            }
        ],
        None,
    )
    preview = preview_mcp_result(result)
    assert preview == "encrypted_result=500chars"
    assert "AAAA" not in preview
