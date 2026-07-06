from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

NEW_SESSION_COMMANDS = frozenset({"/new", "新对话", "新会话"})
HELP_COMMANDS = frozenset({"/help", "帮助"})
CANCEL_PENDING_COMMANDS = frozenset({"/cancel", "取消", "放弃", "取消文件"})


@dataclass(frozen=True)
class UrlVerification:
    challenge: str
    token: str


@dataclass(frozen=True)
class FeishuTextMessage:
    event_id: str
    message_id: str
    chat_id: str
    open_id: str
    text: str
    chat_type: str = "p2p"
    mentions: tuple[dict[str, Any], ...] = ()
    message_type: str = "text"
    file_key: str = ""
    file_name: str = ""

    @property
    def has_file(self) -> bool:
        return self.message_type == "file" and bool(self.file_key)


@dataclass(frozen=True)
class IgnoredEvent:
    reason: str


@dataclass(frozen=True)
class FeishuParseOptions:
    group_enabled: bool = True
    bot_open_id: str = ""


ParseResult = UrlVerification | FeishuTextMessage | IgnoredEvent


def parse_url_verification(payload: dict[str, Any]) -> UrlVerification | None:
    """Parse Feishu URL verification (v1 flat body or v2 schema header/event)."""
    if payload.get("type") == "url_verification":
        challenge = str(payload.get("challenge", "")).strip()
        if not challenge:
            return None
        return UrlVerification(
            challenge=challenge,
            token=str(payload.get("token", "")).strip(),
        )

    header = payload.get("header")
    if not isinstance(header, dict):
        return None
    if str(header.get("event_type", "")).strip() != "url_verification":
        return None

    event = payload.get("event")
    if not isinstance(event, dict):
        return None
    challenge = str(event.get("challenge", "")).strip()
    if not challenge:
        return None
    token = str(header.get("token", "")).strip() or str(event.get("token", "")).strip()
    return UrlVerification(challenge=challenge, token=token)


def parse_event_payload(
    payload: dict[str, Any],
    *,
    options: FeishuParseOptions | None = None,
) -> ParseResult | None:
    opts = options or FeishuParseOptions()
    verification = parse_url_verification(payload)
    if verification is not None:
        return verification

    if payload.get("type") == "url_verification":
        return None

    header = payload.get("header")
    if not isinstance(header, dict):
        return None

    event_type = str(header.get("event_type", "")).strip()
    if event_type != "im.message.receive_v1":
        return IgnoredEvent(f"unsupported event_type={event_type}")

    event = payload.get("event")
    if not isinstance(event, dict):
        return IgnoredEvent("missing event body")

    message = event.get("message")
    if not isinstance(message, dict):
        return IgnoredEvent("missing message")

    chat_type = str(message.get("chat_type", "")).strip()
    if chat_type == "p2p":
        pass
    elif chat_type == "group":
        if not opts.group_enabled:
            return IgnoredEvent("group chat disabled by FEISHU_GROUP_ENABLED=false")
    else:
        return IgnoredEvent(f"unsupported chat_type={chat_type}")

    message_type = str(message.get("message_type", "")).strip()
    if message_type not in {"text", "file"}:
        return IgnoredEvent(f"unsupported message_type={message_type}")

    sender = event.get("sender")
    if isinstance(sender, dict):
        sender_type = str(sender.get("sender_type", "")).strip()
        if sender_type and sender_type != "user":
            return IgnoredEvent(f"ignored sender_type={sender_type}")

    message_id = str(message.get("message_id", "")).strip()
    chat_id = str(message.get("chat_id", "")).strip()
    if not message_id or not chat_id:
        return IgnoredEvent("missing message_id or chat_id")

    open_id = _extract_open_id(sender)
    mentions = _extract_mentions(message.get("mentions"))

    file_key = ""
    file_name = ""
    text = ""

    if message_type == "text":
        raw_text = _extract_text_content(message.get("content"))
        text = strip_mention_tokens(raw_text, mentions)
    else:
        file_key, file_name = _extract_file_content(message.get("content"))

    if chat_type == "group":
        if message_type == "text" and not text:
            return IgnoredEvent("empty text after stripping bot mention")
        if message_type == "file" and not file_key:
            return IgnoredEvent("empty file content")

    elif message_type == "text" and not text:
        return IgnoredEvent("empty text content")
    elif message_type == "file" and not file_key:
        return IgnoredEvent("empty file content")

    event_id = str(header.get("event_id", "")).strip()
    return FeishuTextMessage(
        event_id=event_id,
        message_id=message_id,
        chat_id=chat_id,
        open_id=open_id or chat_id,
        text=text,
        chat_type=chat_type,
        mentions=mentions,
        message_type=message_type,
        file_key=file_key,
        file_name=file_name,
    )


def extract_verification_token(payload: dict[str, Any]) -> str:
    header = payload.get("header")
    if isinstance(header, dict):
        token = str(header.get("token", "")).strip()
        if token:
            return token
    return str(payload.get("token", "")).strip()


def extract_event_type(payload: dict[str, Any]) -> str:
    header = payload.get("header")
    if isinstance(header, dict):
        return str(header.get("event_type", "")).strip()
    if payload.get("type") == "url_verification":
        return "url_verification"
    return ""


def dedupe_key(message: FeishuTextMessage) -> str:
    if message.event_id:
        return f"event:{message.event_id}"
    return f"message:{message.message_id}"


def is_new_session_command(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    lowered = normalized.lower()
    return lowered == "/new" or normalized in NEW_SESSION_COMMANDS


def is_help_command(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    lowered = normalized.lower()
    return lowered == "/help" or normalized in HELP_COMMANDS


def is_cancel_pending_command(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    lowered = normalized.lower()
    return lowered == "/cancel" or normalized in CANCEL_PENDING_COMMANDS


def should_accept_group_message(
    message: FeishuTextMessage,
    *,
    bot_open_id: str,
    require_group_mention: bool,
    group_file_without_mention: bool,
    pending_store: Any | None = None,
    file_intent_keywords: frozenset[str] | None = None,
) -> bool:
    """Return True when a group message should be processed (mention rules applied)."""
    if message.chat_type != "group":
        return True
    if not require_group_mention:
        return True

    bot_id = bot_open_id.strip()
    if bot_id and mentions_include_bot(message.mentions, bot_id):
        return True

    if group_file_without_mention and message.message_type == "file":
        return True

    if message.message_type == "text" and pending_store is not None:
        from .file_intent import mentions_file_keywords

        if pending_store.get_file(message.chat_id, message.open_id) is not None:
            return True
        keywords = file_intent_keywords or frozenset()
        if keywords and mentions_file_keywords(message.text, keywords):
            return True
        if pending_store.has_pending(message.chat_id, message.open_id) and (
            is_cancel_pending_command(message.text) or is_new_session_command(message.text)
        ):
            return True

    log.info(
        "Feishu group mention rejected: message_id=%s message_type=%s has_file=%s "
        "require_mention=%s group_file_without_mention=%s mention_count=%s",
        message.message_id,
        message.message_type,
        message.has_file,
        require_group_mention,
        group_file_without_mention,
        len(message.mentions),
    )
    return False


def _extract_mentions(raw_mentions: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(raw_mentions, list):
        return ()
    mentions: list[dict[str, Any]] = []
    for item in raw_mentions:
        if isinstance(item, dict):
            mentions.append(dict(item))
    return tuple(mentions)


def mentions_include_bot(mentions: tuple[dict[str, Any], ...], bot_open_id: str) -> bool:
    bot_id = bot_open_id.strip()
    if not bot_id:
        return False
    for item in mentions:
        id_obj = item.get("id")
        if not isinstance(id_obj, dict):
            continue
        if str(id_obj.get("open_id", "")).strip() == bot_id:
            return True
    return False


def strip_mention_tokens(text: str, mentions: tuple[dict[str, Any], ...]) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""

    for item in mentions:
        key = str(item.get("key", "")).strip()
        if key:
            cleaned = cleaned.replace(key, " ")
        name = str(item.get("name", "")).strip()
        if name:
            cleaned = cleaned.replace(f"@{name}", " ")

    cleaned = re.sub(r"<at[^>]*>.*?</at>", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _extract_open_id(sender: Any) -> str:
    if not isinstance(sender, dict):
        return ""
    sender_id = sender.get("sender_id")
    if isinstance(sender_id, dict):
        return str(sender_id.get("open_id", "")).strip()
    return ""


def _extract_text_content(raw_content: Any) -> str:
    if raw_content is None:
        return ""
    if isinstance(raw_content, dict):
        return str(raw_content.get("text", "")).strip()
    if not isinstance(raw_content, str):
        return str(raw_content).strip()

    content = raw_content.strip()
    if not content:
        return ""

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return content

    if isinstance(parsed, dict):
        return str(parsed.get("text", "")).strip()
    return content


def _extract_file_content(raw_content: Any) -> tuple[str, str]:
    parsed: dict[str, Any] | None = None
    if isinstance(raw_content, dict):
        parsed = raw_content
    elif isinstance(raw_content, str) and raw_content.strip():
        try:
            loaded = json.loads(raw_content)
        except json.JSONDecodeError:
            loaded = None
        if isinstance(loaded, dict):
            parsed = loaded

    if not parsed:
        return "", ""

    file_key = str(parsed.get("file_key", "")).strip()
    file_name = str(parsed.get("file_name", "")).strip()
    return file_key, file_name
