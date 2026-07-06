"""Tests for nested auth sub-skills under auth-assistant package directory."""

from __future__ import annotations

from pathlib import Path

from access_assistant.skill_loader import SkillLoader

AUTH_SKILL_NAMES = [
    "auth-login-failure",
    "auth-sms-limit",
    "auth-account-info",
    "auth-account-behavior",
    "auth-real-info-limit",
]


def test_auth_assistant_package_discovers_nested_skills():
    skills_root = Path(__file__).resolve().parents[1] / ".claude" / "skills"
    auth_package = skills_root / "auth-assistant"
    assert auth_package.is_dir()
    assert not (auth_package / "SKILL.md").exists(), "package root should have no SKILL.md"

    loader = SkillLoader(
        skill_paths=[auth_package],
        allowed_skill_names=list(AUTH_SKILL_NAMES),
    )
    metadata = loader.scan_skills()
    names = {m.name for m in metadata}

    assert names == set(AUTH_SKILL_NAMES)
    for skill_name in AUTH_SKILL_NAMES:
        content = loader.load_skill(skill_name)
        assert content.metadata.name == skill_name
        assert "auth-mcp-server" in content.metadata.mcp_servers
