from __future__ import annotations

from access_assistant.multi_agent import (
    AUTH_MCP_RETRY_SUFFIX,
    SupervisorSkillsAgent,
    _auth_mcp_tools_used,
    _format_tool_calls_for_log,
)


def test_auth_mcp_tools_used():
    assert _auth_mcp_tools_used(["load_skill", "auth-mcp-server_sqg_query_account_info"])
    assert not _auth_mcp_tools_used(["load_skill", "bash"])


def test_build_task_message_auth_uses_original_question():
    original = "3599948273这个账号为什么登录新百区盟重神兵失败？"
    task = {
        "id": "auth_1",
        "agent": "auth",
        "title": "排查登录失败",
        "instruction": "请查询账号状态、登录记录、操作记录、trace_id……",
        "depends_on": [],
    }
    message = SupervisorSkillsAgent._build_task_message(None, original, task, {})
    assert message == original
    assert "trace_id" not in message


def test_build_task_message_keeps_wrapper_when_dependencies_exist():
    original = "用户问题"
    task = {
        "id": "auth_2",
        "agent": "auth",
        "title": "follow up",
        "instruction": "继续排查",
        "depends_on": ["auth_1"],
    }
    completed = {
        "auth_1": {
            "title": "first",
            "response": "first result",
        }
    }
    message = SupervisorSkillsAgent._build_task_message(None, original, task, completed)
    assert "你正在执行一个多智能体流程中的子任务" in message
    assert "first result" in message


def test_format_tool_calls_for_log():
    assert _format_tool_calls_for_log([]) == "(none)"
    assert "load_skill" in _format_tool_calls_for_log(["load_skill", "bash"])


def test_auth_mcp_retry_suffix_mentions_mcp():
    assert "auth-mcp-server" in AUTH_MCP_RETRY_SUFFIX
