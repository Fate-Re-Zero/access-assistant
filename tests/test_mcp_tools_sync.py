import asyncio

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from access_assistant.mcp_tools import wrap_mcp_tool_for_sync


class _EmptyArgs(BaseModel):
    value: str = Field(default="")


async def _async_only_tool(**kwargs):
    return f"ok:{kwargs.get('value', '')}"


async def _async_content_artifact_tool(**kwargs):
    return ([{"type": "text", "text": f"ok:{kwargs.get('value', '')}"}], {"structured_content": {}})


def test_wrap_mcp_tool_for_sync_content_and_artifact():
    async_tool = StructuredTool(
        name="demo_content_artifact_tool",
        description="content and artifact",
        args_schema=_EmptyArgs,
        coroutine=_async_content_artifact_tool,
        response_format="content_and_artifact",
    )

    wrapped = wrap_mcp_tool_for_sync(async_tool)
    output = wrapped.invoke({"value": "tuple"})
    assert output is not None
    assert "ok:tuple" in str(output)


def test_wrap_mcp_tool_for_sync_allows_invoke():
    async_tool = StructuredTool(
        name="demo_async_tool",
        description="async only",
        args_schema=_EmptyArgs,
        coroutine=_async_only_tool,
    )

    wrapped = wrap_mcp_tool_for_sync(async_tool)
    assert wrapped.func is not None
    assert wrapped.coroutine is not None
    assert wrapped.invoke({"value": "test"}) == "ok:test"


def test_wrap_mcp_tool_for_sync_allows_async_invoke():
    async_tool = StructuredTool(
        name="demo_async_tool",
        description="async only",
        args_schema=_EmptyArgs,
        coroutine=_async_only_tool,
    )

    wrapped = wrap_mcp_tool_for_sync(async_tool)
    result = asyncio.run(wrapped.ainvoke({"value": "async"}))
    assert result == "ok:async"


def test_wrap_mcp_tool_for_sync_logs_tool_trace(caplog, monkeypatch):
    import logging

    monkeypatch.setenv("TOOL_CALL_LOG", "true")
    caplog.set_level(logging.INFO, logger="access_assistant.tool_trace")

    async_tool = StructuredTool(
        name="auth-mcp-server_demo_query",
        description="async only",
        args_schema=_EmptyArgs,
        coroutine=_async_only_tool,
    )
    wrapped = wrap_mcp_tool_for_sync(async_tool)
    wrapped.invoke({"value": "trace"})

    messages = [record.getMessage() for record in caplog.records]
    call_lines = [message for message in messages if "[TOOL CALL]" in message]
    assert len(call_lines) == 1
    assert "auth-mcp-server_demo_query" in call_lines[0]
    assert "mcp_args=" in call_lines[0]
    assert "trace" in call_lines[0]
    assert any("[TOOL RESULT]" in message and "auth-mcp-server_demo_query" in message for message in messages)
