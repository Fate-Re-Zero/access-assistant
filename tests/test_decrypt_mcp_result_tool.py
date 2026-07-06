from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from access_assistant.auth_crypto.decrypt import encrypt
from access_assistant.skill_loader import SkillLoader
from access_assistant.tools import AUTH_AGENT_TOOLS, SkillAgentContext, decrypt_mcp_result


def _make_runtime(tmp_path: Path) -> SimpleNamespace:
    context = SkillAgentContext(
        skill_loader=MagicMock(spec=SkillLoader),
        working_directory=tmp_path,
        active_thread_id="thread-auth_1",
    )
    return SimpleNamespace(context=context)


def test_auth_agent_tools_include_decrypt_mcp_result():
    tool_names = [tool.name for tool in AUTH_AGENT_TOOLS]
    assert tool_names == ["load_skill", "decrypt_mcp_result"]
    assert "bash" not in tool_names
    assert "write_file" not in tool_names


def test_decrypt_mcp_result_cipher(tmp_path: Path):
    plain = "账号状态正常"
    runtime = _make_runtime(tmp_path)

    result = decrypt_mcp_result.func(runtime, cipher=encrypt(plain))

    assert result.startswith("[OK]")
    assert plain in result


def test_decrypt_mcp_result_json_payload(tmp_path: Path):
    plain = "最近登录失败 3 次"
    runtime = _make_runtime(tmp_path)
    payload = json.dumps({"result": encrypt(plain)})

    result = decrypt_mcp_result.func(runtime, json_payload=payload)

    assert result.startswith("[OK]")
    assert plain in result


def test_decrypt_mcp_result_json_file(tmp_path: Path):
    plain = "操作记录：改密"
    runtime = _make_runtime(tmp_path)
    json_path = tmp_path / "mcp_response.json"
    json_path.write_text(json.dumps({"result": encrypt(plain)}), encoding="utf-8")

    result = decrypt_mcp_result.func(runtime, json_file="mcp_response.json")

    assert result.startswith("[OK]")
    assert plain in result


def test_decrypt_mcp_result_batch(tmp_path: Path):
    plain_a = "账号状态正常"
    plain_b = "最近登录失败 3 次"
    file_a = tmp_path / "mcp_account_info.json"
    file_b = tmp_path / "mcp_login_behavior.json"
    file_a.write_text(json.dumps({"result": encrypt(plain_a)}), encoding="utf-8")
    file_b.write_text(json.dumps({"result": encrypt(plain_b)}), encoding="utf-8")
    runtime = _make_runtime(tmp_path)

    result = decrypt_mcp_result.func(
        runtime,
        json_files="mcp_account_info.json,mcp_login_behavior.json",
        labels="account_info,login_behavior",
    )

    assert result.startswith("[OK]")
    assert "=== account_info ===" in result
    assert "=== login_behavior ===" in result
    assert plain_a in result
    assert plain_b in result


def test_decrypt_mcp_result_rejects_multiple_modes(tmp_path: Path):
    runtime = _make_runtime(tmp_path)

    result = decrypt_mcp_result.func(runtime, cipher="abc", json_payload='{"result":"abc"}')

    assert result.startswith("[FAILED]")
    assert "only one" in result.lower()


def test_decrypt_mcp_result_invalid_cipher(tmp_path: Path):
    runtime = _make_runtime(tmp_path)

    result = decrypt_mcp_result.func(runtime, cipher="not-valid-cipher")

    assert result.startswith("[FAILED]")
    assert "decrypt failed" in result
