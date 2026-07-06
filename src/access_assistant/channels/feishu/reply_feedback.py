from __future__ import annotations

import time
from typing import Any, Literal

from .events import extract_event_type

FEEDBACK_ACTION = "reply_feedback"
FEEDBACK_HELPFUL = "helpful"
FEEDBACK_UNHELPFUL = "unhelpful"
FeedbackKind = Literal["helpful", "unhelpful"]

FEEDBACK_THANKS_TEXT = "感谢你的宝贵建议，我们会继续努力"
FEEDBACK_HELPFUL_LABEL = "👍 有帮助"
FEEDBACK_UNHELPFUL_LABEL = "👎 无帮助"

_REPLY_CONTENT_CACHE: dict[str, tuple[str, float]] = {}
_REPLY_CONTENT_CACHE_TTL_SECONDS = 86_400.0
_REPLY_CONTENT_CACHE_MAX_SIZE = 5000


def store_reply_card_content(message_id: str, lark_md: str) -> None:
    """Remember card body so feedback callbacks can refresh the original answer."""
    normalized_id = message_id.strip()
    normalized_md = lark_md.strip()
    if not normalized_id or not normalized_md:
        return
    now = time.time()
    _REPLY_CONTENT_CACHE[normalized_id] = (normalized_md, now)
    if len(_REPLY_CONTENT_CACHE) <= _REPLY_CONTENT_CACHE_MAX_SIZE:
        return
    expired = [
        key
        for key, (_, saved_at) in _REPLY_CONTENT_CACHE.items()
        if now - saved_at > _REPLY_CONTENT_CACHE_TTL_SECONDS
    ]
    for key in expired:
        _REPLY_CONTENT_CACHE.pop(key, None)
    while len(_REPLY_CONTENT_CACHE) > _REPLY_CONTENT_CACHE_MAX_SIZE:
        oldest_key = min(_REPLY_CONTENT_CACHE, key=lambda k: _REPLY_CONTENT_CACHE[k][1])
        _REPLY_CONTENT_CACHE.pop(oldest_key, None)


def get_reply_card_content(message_id: str) -> str:
    normalized_id = message_id.strip()
    if not normalized_id:
        return ""
    entry = _REPLY_CONTENT_CACHE.get(normalized_id)
    if entry is None:
        return ""
    lark_md, saved_at = entry
    if time.time() - saved_at > _REPLY_CONTENT_CACHE_TTL_SECONDS:
        _REPLY_CONTENT_CACHE.pop(normalized_id, None)
        return ""
    return lark_md


def pop_reply_card_content(message_id: str) -> str:
    content = get_reply_card_content(message_id)
    if content:
        _REPLY_CONTENT_CACHE.pop(message_id.strip(), None)
    return content


def extract_message_id_from_send_response(data: dict[str, Any]) -> str:
    payload = data.get("data")
    if isinstance(payload, dict):
        message_id = str(payload.get("message_id", "")).strip()
        if message_id:
            return message_id
    return ""


def build_feedback_button(
    feedback: FeedbackKind,
    *,
    selected: bool = False,
    use_callback: bool = True,
) -> dict[str, Any]:
    label = FEEDBACK_HELPFUL_LABEL if feedback == FEEDBACK_HELPFUL else FEEDBACK_UNHELPFUL_LABEL
    button: dict[str, Any] = {
        "tag": "button",
        "text": {
            "tag": "plain_text",
            "content": label,
        },
        "type": "primary" if selected else "default",
        "size": "medium",
        "width": "default",
        "disabled": selected,
    }
    if use_callback:
        button["behaviors"] = [
            {
                "type": "callback",
                "value": {
                    "action": FEEDBACK_ACTION,
                    "feedback": feedback,
                },
            }
        ]
    return button


def build_feedback_action_row(
    *,
    selected: FeedbackKind | None = None,
    use_callback: bool = True,
) -> dict[str, Any]:
    """Centered feedback buttons via column_set (reliable button chrome in Feishu 2.0)."""
    return {
        "tag": "column_set",
        "flex_mode": "none",
        "horizontal_align": "center",
        "margin": "8px 0 0 0",
        "columns": [
            {
                "tag": "column",
                "width": "auto",
                "elements": [
                    build_feedback_button(
                        FEEDBACK_HELPFUL,
                        selected=selected == FEEDBACK_HELPFUL,
                        use_callback=use_callback,
                    ),
                ],
            },
            {
                "tag": "column",
                "width": "auto",
                "elements": [
                    build_feedback_button(
                        FEEDBACK_UNHELPFUL,
                        selected=selected == FEEDBACK_UNHELPFUL,
                        use_callback=use_callback,
                    ),
                ],
            },
        ],
    }


def build_interactive_card(
    lark_md: str,
    *,
    include_feedback: bool = False,
    feedback_use_callback: bool = False,
    feedback_selected: FeedbackKind | None = None,
    show_thanks: bool = False,
) -> dict[str, Any]:
    elements: list[dict[str, Any]] = []
    body_md = lark_md.strip()
    if body_md:
        elements.append({"tag": "markdown", "content": body_md})
    if show_thanks:
        elements.append(
            {
                "tag": "markdown",
                "content": f"<font color='grey'>{FEEDBACK_THANKS_TEXT}</font>",
            }
        )
    if include_feedback or feedback_selected is not None:
        elements.append(
            build_feedback_action_row(
                selected=feedback_selected,
                use_callback=feedback_use_callback,
            )
        )
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "body": {"elements": elements},
    }


def parse_reply_feedback_callback(payload: dict[str, Any]) -> FeedbackKind | None:
    if extract_event_type(payload) != "card.action.trigger":
        return None

    action_value = _extract_card_action_value(payload)
    if str(action_value.get("action", "")).strip() != FEEDBACK_ACTION:
        return None

    feedback = str(action_value.get("feedback", "")).strip().lower()
    if feedback == FEEDBACK_HELPFUL:
        return FEEDBACK_HELPFUL
    if feedback == FEEDBACK_UNHELPFUL:
        return FEEDBACK_UNHELPFUL
    return None


def extract_card_callback_message_id(payload: dict[str, Any]) -> str:
    event = payload.get("event")
    if isinstance(event, dict):
        context = event.get("context")
        if isinstance(context, dict):
            message_id = str(context.get("open_message_id", "")).strip()
            if message_id:
                return message_id
    context = payload.get("context")
    if isinstance(context, dict):
        message_id = str(context.get("open_message_id", "")).strip()
        if message_id:
            return message_id
    return ""


def build_feedback_callback_response(
    feedback: FeedbackKind,
    *,
    original_lark_md: str = "",
) -> dict[str, Any]:
    card = build_interactive_card(
        original_lark_md,
        include_feedback=True,
        feedback_use_callback=True,
        feedback_selected=feedback,
        show_thanks=True,
    )
    return {
        "toast": {
            "type": "success",
            "content": FEEDBACK_THANKS_TEXT,
        },
        "card": {
            "type": "raw",
            "data": card,
        },
    }


def _extract_card_action_value(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("event")
    if isinstance(event, dict):
        action = event.get("action")
        if isinstance(action, dict):
            value = action.get("value")
            if isinstance(value, dict):
                return value
    action = payload.get("action")
    if isinstance(action, dict):
        value = action.get("value")
        if isinstance(value, dict):
            return value
    return {}
