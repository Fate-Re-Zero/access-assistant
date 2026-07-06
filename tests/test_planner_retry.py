from __future__ import annotations

from access_assistant.multi_agent import _is_planner_transient_error


def test_planner_transient_error_connection_message():
    assert _is_planner_transient_error(RuntimeError("Connection error.")) is True


def test_planner_transient_error_timeout_type():
    assert _is_planner_transient_error(TimeoutError("read timed out")) is True


def test_planner_non_transient_error():
    assert _is_planner_transient_error(ValueError("invalid json")) is False
