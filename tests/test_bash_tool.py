from __future__ import annotations

from pathlib import Path

from access_assistant.tools import _decode_process_output, _normalize_bash_command


def test_decode_process_output_prefers_utf8() -> None:
    assert _decode_process_output("hello 中文".encode("utf-8")) == "hello 中文"


def test_decode_process_output_falls_back_to_gbk() -> None:
    text = "账号状态"
    assert _decode_process_output(text.encode("gb18030")) == text


def test_normalize_bash_command_drops_redundant_cd_when_already_in_project() -> None:
    cwd = Path("D:/payment_center_agent/agent-admin/access-assistant")
    command = "cd access-assistant && uv run auth-decrypt decrypt --cipher abc"
    assert _normalize_bash_command(command, cwd) == "uv run auth-decrypt decrypt --cipher abc"


def test_normalize_bash_command_keeps_cd_when_nested_project_exists() -> None:
    cwd = Path("D:/payment_center_agent/agent-admin")
    nested = cwd / "access-assistant"
    if not nested.is_dir():
        return
    command = "cd access-assistant && uv run auth-decrypt decrypt --cipher abc"
    assert _normalize_bash_command(command, cwd) == command
