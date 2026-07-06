from __future__ import annotations

from access_assistant.channels.feishu.files import (
    build_file_agent_prompt,
    decode_text_bytes,
    is_allowed_text_file,
    truncate_for_prompt,
)

ALLOWED = frozenset({".txt", ".md", ".markdown"})


def test_is_allowed_text_file():
    assert is_allowed_text_file("notes.md", ALLOWED) is True
    assert is_allowed_text_file("README.MD", ALLOWED) is True
    assert is_allowed_text_file("doc.pdf", ALLOWED) is False
    assert is_allowed_text_file("noext", ALLOWED) is False


def test_decode_text_bytes_utf8_and_gbk():
    assert decode_text_bytes("你好".encode("utf-8")) == "你好"
    assert decode_text_bytes("中文".encode("gbk")) == "中文"


def test_truncate_for_prompt():
    text, truncated = truncate_for_prompt("abcdef", 3)
    assert text == "abc"
    assert truncated is True


def test_build_file_agent_prompt():
    prompt = build_file_agent_prompt(
        file_name="demo.md",
        file_content="# Title\nbody",
        user_text="请总结",
        truncated=False,
    )
    assert "[用户上传文件: demo.md]" in prompt
    assert "用户附言: 请总结" in prompt
    assert "# Title" in prompt
