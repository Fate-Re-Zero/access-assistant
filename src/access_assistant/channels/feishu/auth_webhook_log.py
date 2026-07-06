from __future__ import annotations

import logging
from typing import Any

from .events import FeishuTextMessage, mentions_include_bot

log = logging.getLogger(__name__)

AUTH_WEBHOOK_LOG_PREFIX = "[AUTH-WEBHOOK]"


def log_auth_webhook(step: str, outcome: str, **fields: Any) -> None:
    """Structured auth webhook log line for end-to-end troubleshooting."""
    parts = [f"{AUTH_WEBHOOK_LOG_PREFIX} step={step}", f"outcome={outcome}"]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    message = " ".join(parts)
    if outcome in {"error", "rejected", "mismatch", "failed"}:
        log.warning(message)
    else:
        log.info(message)


def mention_open_ids(message: FeishuTextMessage) -> list[str]:
    open_ids: list[str] = []
    for item in message.mentions:
        id_obj = item.get("id")
        if isinstance(id_obj, dict):
            open_id = str(id_obj.get("open_id", "")).strip()
            if open_id:
                open_ids.append(open_id)
    return open_ids


def log_auth_access_check(
    *,
    chat_id: str,
    chat_type: str,
    allowed_chat_ids: frozenset[str],
    p2p_enabled: bool = True,
) -> bool:
    """Log auth webhook access decision; return True when the chat is allowed."""
    if chat_type != "group":
        if not p2p_enabled:
            log_auth_webhook(
                "access",
                "rejected",
                chat_id=chat_id,
                chat_type=chat_type,
                p2p_enabled=False,
                reason="p2p_disabled",
            )
            return False
        log_auth_webhook(
            "access",
            "allowed",
            chat_id=chat_id,
            chat_type=chat_type,
            p2p_enabled=True,
            reason="p2p_allowed",
        )
        return True
    if not allowed_chat_ids:
        log_auth_webhook(
            "access",
            "allowed",
            chat_id=chat_id,
            chat_type=chat_type,
            reason="group_whitelist_disabled",
        )
        return True
    in_whitelist = chat_id in allowed_chat_ids
    log_auth_webhook(
        "access",
        "allowed" if in_whitelist else "rejected",
        chat_id=chat_id,
        chat_type=chat_type,
        configured_chat_ids=sorted(allowed_chat_ids),
        in_whitelist=in_whitelist,
        reason="group_in_whitelist" if in_whitelist else "group_not_in_whitelist",
    )
    return in_whitelist


def log_auth_whitelist_check(
    *,
    chat_id: str,
    chat_type: str,
    allowed_chat_ids: frozenset[str],
    p2p_enabled: bool = True,
) -> bool:
    """Backward-compatible alias for log_auth_access_check."""
    return log_auth_access_check(
        chat_id=chat_id,
        chat_type=chat_type,
        allowed_chat_ids=allowed_chat_ids,
        p2p_enabled=p2p_enabled,
    )


def log_auth_mention_check(
    *,
    message: FeishuTextMessage,
    bot_open_id: str,
    accepted: bool,
    require_mention: bool,
    group_file_without_mention: bool,
) -> None:
    mentioned_bot = mentions_include_bot(message.mentions, bot_open_id) if bot_open_id else False
    log_auth_webhook(
        "mention",
        "accepted" if accepted else "rejected",
        message_id=message.message_id,
        chat_id=message.chat_id,
        bot_open_id=bot_open_id or "(unset)",
        require_mention=require_mention,
        group_file_without_mention=group_file_without_mention,
        mention_count=len(message.mentions),
        mention_open_ids=mention_open_ids(message) or "(none)",
        mentioned_bot=mentioned_bot,
        message_type=message.message_type,
        has_file=message.has_file,
        reason="mention_ok" if accepted else "bot_not_mentioned",
    )
