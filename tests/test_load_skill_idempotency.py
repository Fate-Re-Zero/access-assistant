from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from access_assistant.skill_loader import SkillContent, SkillMetadata
from access_assistant.tools import SkillAgentContext, load_skill


def _make_runtime(*, thread_id: str = "thread-auth_1") -> tuple[SimpleNamespace, MagicMock]:
    metadata = SkillMetadata(
        name="auth-login-failure",
        description="auth login failure skill",
        skill_path=Path("/tmp/auth-assistant/login-failure"),
        mcp_servers=["auth-mcp-server"],
    )
    skill_content = SkillContent(
        metadata=metadata,
        instructions="Use MCP tools for account lookup.",
    )
    loader = MagicMock()
    loader.load_skill.return_value = skill_content

    context = SkillAgentContext(
        skill_loader=loader,
        working_directory=Path("/tmp"),
        active_thread_id=thread_id,
    )
    runtime = SimpleNamespace(context=context)
    return runtime, loader


def test_load_skill_is_idempotent_within_same_thread():
    runtime, loader = _make_runtime()

    first = load_skill.func("auth-login-failure", runtime=runtime)
    second = load_skill.func("auth-login-failure", runtime=runtime)

    assert "Use MCP tools" in first
    assert loader.load_skill.call_count == 1
    assert "already loaded" in second.lower()
    assert "do not call load_skill again" in second.lower()


def test_load_skill_allows_reload_for_different_thread():
    runtime_a, loader = _make_runtime(thread_id="thread-a")
    runtime_b, _ = _make_runtime(thread_id="thread-b")
    runtime_b.context.skill_loader = loader

    load_skill.func("auth-login-failure", runtime=runtime_a)
    load_skill.func("auth-login-failure", runtime=runtime_b)

    assert loader.load_skill.call_count == 2
