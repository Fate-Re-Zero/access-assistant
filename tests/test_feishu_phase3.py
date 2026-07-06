from __future__ import annotations

from access_assistant.channels.feishu.formatting import markdown_to_lark_md, split_lark_md
from access_assistant.channels.feishu.progress import format_progress_event, is_progress_event


def test_markdown_to_lark_md():
    source = "# 标题\n\n- 条目一\n\n**加粗**"
    converted = markdown_to_lark_md(source)
    assert "**标题**" in converted
    assert "- 条目一" in converted
    assert "**加粗**" in converted


def test_split_lark_md():
    chunks = split_lark_md("a" * 5000, chunk_size=2000)
    assert len(chunks) == 3


def test_format_progress_event():
    event = {
        "type": "agent_call",
        "agent_name": "payment",
        "title": "Payment Agent",
    }
    text = format_progress_event(event)
    assert text is not None
    assert "Payment Agent" in text
    assert is_progress_event(event)
