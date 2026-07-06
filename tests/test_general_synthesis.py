from __future__ import annotations

from access_assistant.multi_agent import (
    _resolve_int_env,
    _truncate_for_synthesis,
    SupervisorSkillsAgent,
)


def test_truncate_for_synthesis_short_text_unchanged():
    text = "简短结论"
    assert _truncate_for_synthesis(text, 2000) == text


def test_truncate_for_synthesis_long_text():
    text = "x" * 2500
    result = _truncate_for_synthesis(text, 2000)
    assert len(result) > 2000
    assert "已截断" in result
    assert "2500" in result


def test_resolve_int_env_invalid_falls_back():
    import os

    os.environ["TEST_INT_ENV_BAD"] = "not-a-number"
    assert _resolve_int_env("TEST_INT_ENV_BAD", 99) == 99
    os.environ.pop("TEST_INT_ENV_BAD", None)


def test_build_task_message_general_passthrough():
    agent = object.__new__(SupervisorSkillsAgent)
    task = {"id": "t1", "agent": "general", "title": "问候", "instruction": "回复", "depends_on": []}
    message = agent._build_task_message("你有哪些能力？", task, {})
    assert message == "你有哪些能力？"


def test_build_task_message_general_with_deps_uses_wrapper():
    agent = object.__new__(SupervisorSkillsAgent)
    task = {
        "id": "t2",
        "agent": "general",
        "title": "汇总前置",
        "instruction": "整理",
        "depends_on": ["t1"],
    }
    completed = {
        "t1": {"id": "t1", "title": "支付", "response": "支付结论", "success": True, "agent": "payment"},
    }
    message = agent._build_task_message("订单问题", task, completed)
    assert "你正在执行一个多智能体流程中的子任务" in message
    assert "支付结论" in message


def test_build_task_synthesis_prompt_truncates_responses():
    agent = object.__new__(SupervisorSkillsAgent)
    agent.synthesis_input_max_chars = 100
    completed = {
        "a": {
            "id": "a",
            "title": "支付排查",
            "agent": "payment",
            "success": True,
            "response": "p" * 500,
        },
    }
    prompt = agent._build_task_synthesis_prompt("订单不到账", completed)
    assert "已截断" in prompt
    assert "400 字" in prompt
    assert "Agent：" not in prompt
