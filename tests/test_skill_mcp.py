from access_assistant.skill_loader import _parse_mcp_servers, SkillLoader


def test_parse_mcp_servers_from_list():
    assert _parse_mcp_servers(["payment-mcp-server", "logs-mcp-server"]) == [
        "payment-mcp-server",
        "logs-mcp-server",
    ]


def test_parse_mcp_servers_from_string():
    assert _parse_mcp_servers("payment-mcp-server") == ["payment-mcp-server"]


def test_skill_metadata_includes_mcp_servers(tmp_path):
    skill_dir = tmp_path / "payment-assistant"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: payment-assistant
description: payment skill
mcp_servers:
  - payment-mcp-server
---

# Payment
""",
        encoding="utf-8",
    )
    loader = SkillLoader([skill_dir])
    skills = loader.scan_skills()
    assert len(skills) == 1
    assert skills[0].mcp_servers == ["payment-mcp-server"]
