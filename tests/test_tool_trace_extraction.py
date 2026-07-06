from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from access_assistant.agent import AccessAssistantAgent
from access_assistant.stage_io_log import _summarize_value


def test_extract_tool_names_respects_after_index():
    history = [
        HumanMessage(content="old question"),
        AIMessage(content="", tool_calls=[{"name": "auth-mcp-server_old_tool", "id": "1", "args": {}}]),
        ToolMessage(content="old result", tool_call_id="1", name="auth-mcp-server_old_tool"),
    ]
    current = [
        HumanMessage(content="new question"),
        AIMessage(content="", tool_calls=[{"name": "load_skill", "id": "2", "args": {}}]),
        ToolMessage(content="skill loaded", tool_call_id="2", name="load_skill"),
    ]
    result = {"messages": history + current}

    all_names = AccessAssistantAgent.extract_tool_names_from_result(result)
    assert "auth-mcp-server_old_tool" in all_names
    assert "load_skill" in all_names

    turn_names = AccessAssistantAgent.extract_tool_names_from_result(result, after_index=len(history))
    assert turn_names == ["load_skill", "load_skill"]
    assert "auth-mcp-server_old_tool" not in turn_names


def test_stage_io_summarize_tool_calls_shows_names():
    short = _summarize_value("tool_calls", "load_skill,bash")
    assert short == "load_skill,bash"

    long = _summarize_value(
        "tool_calls",
        ",".join([f"tool_{index}" for index in range(20)]),
    )
    assert long.startswith("20tools:")
    assert "tool_0" in long
