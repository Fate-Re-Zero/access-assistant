from __future__ import annotations

from access_assistant.multi_agent import _resolve_int_env


def test_auth_max_tokens_env_default(monkeypatch):
    monkeypatch.delenv("AUTH_MAX_TOKENS", raising=False)
    assert _resolve_int_env("AUTH_MAX_TOKENS", 1600) == 1600


def test_auth_max_tokens_env_override(monkeypatch):
    monkeypatch.setenv("AUTH_MAX_TOKENS", "800")
    assert _resolve_int_env("AUTH_MAX_TOKENS", 1600) == 800


def test_auth_agent_prompt_contains_shared_rules():
    from access_assistant.multi_agent import AUTH_AGENT_PROMPT, AUTH_SKILL_NAMES

    assert "decrypt_mcp_result" in AUTH_AGENT_PROMPT
    assert "load_skill" in AUTH_AGENT_PROMPT
    assert "auth-login-failure" in AUTH_SKILL_NAMES
    assert "auth-real-info-limit" in AUTH_SKILL_NAMES
    assert "禁止" in AUTH_AGENT_PROMPT and "bash" in AUTH_AGENT_PROMPT
    assert "最多 2 次" in AUTH_AGENT_PROMPT
