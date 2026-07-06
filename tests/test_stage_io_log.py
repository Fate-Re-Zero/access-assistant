from __future__ import annotations

from access_assistant.stage_io_log import _summarize_fields, _summarize_value


def test_summarize_bulk_text_fields():
    assert _summarize_value("message", "hello world") == "11chars"
    assert _summarize_value("response", "x" * 500) == "500chars"


def test_summarize_plan():
    plan = {
        "reason": "auth issue",
        "tasks": [{"id": "auth_1", "agent": "auth"}],
    }
    summary = _summarize_value("plan", plan)
    assert "auth_1:auth" in summary
    assert "auth issue" in summary


def test_summarize_fields_single_line():
    text = _summarize_fields(
        {
            "message": "3599948273登录失败",
            "max_retries": 2,
            "success": True,
        }
    )
    assert "\n" not in text
    assert "message=" in text
    assert "chars" in text
