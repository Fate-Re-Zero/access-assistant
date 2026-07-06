from __future__ import annotations

from typing import Any, Iterator

from access_assistant.multi_agent import SupervisorSkillsAgent


def _bare_supervisor(**attrs: Any) -> SupervisorSkillsAgent:
    agent = object.__new__(SupervisorSkillsAgent)
    for key, value in attrs.items():
        setattr(agent, key, value)
    return agent


def _fake_task_batch(
    batch: list[dict[str, Any]], *_args: Any, **_kwargs: Any
) -> Iterator[dict[str, Any]]:
    if False:
        yield {}
    return {
        task["id"]: {
            "id": task["id"],
            "agent": task["agent"],
            "title": task["title"],
            "response": "ok",
            "success": True,
            "display_name": "Auth Agent",
        }
        for task in batch
    }


def test_task_plan_progress_when_thinking_disabled():
    supervisor = _bare_supervisor(
        show_planner_progress=True,
        enable_thinking=False,
        show_subagent_progress=False,
        max_parallel_tasks=3,
        _stream_task_batch=_fake_task_batch,
    )
    plan = {
        "reason": "这是明确的账号登录失败排查问题，属于 auth 单域问题。",
        "tasks": [
            {
                "id": "auth_1",
                "agent": "auth",
                "title": "排查登录失败",
                "instruction": "排查账号登录失败",
                "depends_on": [],
            }
        ],
    }

    events = list(
        supervisor._stream_plan_execution(
            message="liaofeng163@qq.com 登录失败",
            thread_id="thread-test",
            plan=plan,
            planner_raw_output="{}",
            planner_fallback_used=False,
        )
    )
    thinking = [event for event in events if event.get("type") == "thinking"]

    assert len(thinking) == 1
    assert "[task plan]" in str(thinking[0].get("content", ""))
    assert "auth 单域问题" in str(thinking[0].get("content", ""))


def test_task_plan_progress_hidden_when_planner_progress_disabled():
    supervisor = _bare_supervisor(
        show_planner_progress=False,
        enable_thinking=True,
        show_subagent_progress=False,
        max_parallel_tasks=3,
        _stream_task_batch=_fake_task_batch,
    )
    plan = {
        "reason": "auth 单域问题",
        "tasks": [
            {
                "id": "auth_1",
                "agent": "auth",
                "title": "排查登录失败",
                "instruction": "排查账号登录失败",
                "depends_on": [],
            }
        ],
    }

    events = list(
        supervisor._stream_plan_execution(
            message="登录失败",
            thread_id="thread-test",
            plan=plan,
            planner_raw_output="{}",
            planner_fallback_used=False,
        )
    )
    thinking = [event for event in events if event.get("type") == "thinking"]

    assert thinking == []
