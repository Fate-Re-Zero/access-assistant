from __future__ import annotations

import json

from access_assistant.multi_agent import (
    AGENT_REGISTRY,
    SupervisorSkillsAgent,
    _build_planner_user_prompt,
    build_planner_direct_response,
)


def _agent() -> SupervisorSkillsAgent:
    return object.__new__(SupervisorSkillsAgent)


def test_parse_direct_greeting_plan():
    payload = {
        "reason": "用户打招呼，直接回复即可。",
        "reply_mode": "direct",
        "direct_kind": "greeting",
        "tasks": [],
    }
    plan = _agent()._parse_task_plan_response(json.dumps(payload))
    assert plan is not None
    assert plan["reply_mode"] == "direct"
    assert plan["direct_kind"] == "greeting"
    assert plan["tasks"] == []


def test_parse_direct_capabilities_plan():
    payload = {
        "reason": "用户询问助手能力。",
        "reply_mode": "direct",
        "direct_kind": "capabilities",
        "tasks": [],
    }
    plan = _agent()._parse_task_plan_response(json.dumps(payload))
    assert plan is not None
    assert plan["reply_mode"] == "direct"
    assert plan["direct_kind"] == "capabilities"


def test_parse_empty_tasks_without_direct_mode_returns_none():
    payload = {"reason": "missing mode", "tasks": []}
    assert _agent()._parse_task_plan_response(json.dumps(payload)) is None


def test_build_greeting_response_includes_registry_capabilities():
    text = build_planner_direct_response("你好", direct_kind="greeting")
    assert "你好！很高兴为你服务。" in text
    assert "支付子智能体" in text
    assert "认证子智能体" in text
    assert "请直接描述你的具体问题" in text


def test_build_capabilities_response_skips_greeting_line():
    text = build_planner_direct_response("你有哪些能力", direct_kind="capabilities")
    assert "你好！很高兴为你服务。" not in text
    assert "我可以协调以下子智能体" in text


def test_direct_response_excludes_general_agent():
    text = build_planner_direct_response("你好")
    general = next(item for item in AGENT_REGISTRY if item["key"] == "general")
    assert general["display_name"] not in text


def test_is_direct_reply_plan():
    agent = _agent()
    assert agent._is_direct_reply_plan({"reply_mode": "direct", "tasks": []}) is True
    assert agent._is_direct_reply_plan({"reply_mode": "tasks", "tasks": [{"id": "g1"}]}) is False


def test_build_planner_user_prompt_greeting_only():
    assert _build_planner_user_prompt("你好") == "你好"


def test_build_planner_user_prompt_with_history():
    prompt = _build_planner_user_prompt(
        "继续查",
        conversation_history="用户：订单 123 没到账\n助手：请提供商户单号",
    )
    assert "【对话历史】" in prompt
    assert "【当前问题】" in prompt
    assert prompt.endswith("继续查")


def test_execute_plan_sync_direct_short_circuit():
    agent = _agent()
    plan = {
        "reason": "问候",
        "reply_mode": "direct",
        "direct_kind": "greeting",
        "tasks": [],
    }
    state = agent._execute_plan_sync(
        message="你好",
        thread_id="t1",
        plan=plan,
        planner_raw_output="{}",
        planner_fallback_used=False,
    )
    assert state["final_response"].startswith("你好！很高兴为你服务。")
    assert state["planned_tasks"] == []
    assert state["task_results"] == {}
