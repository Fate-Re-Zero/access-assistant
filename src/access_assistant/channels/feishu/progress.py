from __future__ import annotations

from typing import Any

AGENT_DISPLAY_NAMES = {
    "payment": "Payment Agent",
    "integration": "Integration Agent",
    "auth": "Auth Agent",
    "knowledge": "Knowledge Agent",
    "general": "General Agent",
    "supervisor": "Supervisor",
}


def format_progress_event(event: dict[str, Any]) -> str | None:
    event_type = str(event.get("type", "")).strip()
    if event_type == "thinking":
        content = str(event.get("content", "")).strip()
        return content or "正在规划任务…"

    if event_type == "agent_call":
        agent_name = str(event.get("agent_name") or event.get("agent") or "").strip()
        title = str(event.get("title") or event.get("task_title") or "").strip()
        label = title or AGENT_DISPLAY_NAMES.get(agent_name, agent_name) or "子智能体"
        return f"**{label}** 处理中…"

    if event_type == "tool_call":
        tool_name = str(event.get("name") or event.get("tool_name") or "工具").strip()
        return f"调用工具 `{tool_name}`…"

    if event_type in {"agent_error", "error"}:
        message = str(event.get("message") or event.get("content") or "执行异常").strip()
        return f"执行异常：{message}"

    return None


def is_progress_event(event: dict[str, Any]) -> bool:
    return format_progress_event(event) is not None
