from __future__ import annotations

from access_assistant.channels.feishu.config import FeishuConfig
from access_assistant.channels.feishu.dedupe import EventDeduper
from access_assistant.channels.feishu.events import (
    FeishuParseOptions,
    FeishuTextMessage,
    IgnoredEvent,
    UrlVerification,
    dedupe_key,
    is_help_command,
    is_new_session_command,
    mentions_include_bot,
    parse_event_payload,
    should_accept_group_message,
    strip_mention_tokens,
)
from access_assistant.channels.feishu.pending import FeishuPendingStore, PendingFile
from access_assistant.channels.feishu.session import FeishuSessionStore

BOT_OPEN_ID = "ou_bot_app"


def test_parse_url_verification():
    parsed = parse_event_payload(
        {
            "challenge": "abc123",
            "token": "verify-token",
            "type": "url_verification",
        }
    )
    assert isinstance(parsed, UrlVerification)
    assert parsed.challenge == "abc123"
    assert parsed.token == "verify-token"


def test_parse_p2p_file_message():
    parsed = parse_event_payload(
        {
            "schema": "2.0",
            "header": {
                "event_id": "evt_file",
                "event_type": "im.message.receive_v1",
            },
            "event": {
                "sender": {
                    "sender_type": "user",
                    "sender_id": {"open_id": "ou_test_user"},
                },
                "message": {
                    "message_id": "om_file",
                    "chat_id": "oc_test_chat",
                    "chat_type": "p2p",
                    "message_type": "file",
                    "content": '{"file_key":"file_v2_abc","file_name":"notes.md"}',
                },
            },
        }
    )
    assert isinstance(parsed, FeishuTextMessage)
    assert parsed.message_type == "file"
    assert parsed.file_key == "file_v2_abc"
    assert parsed.file_name == "notes.md"
    assert parsed.has_file is True


def test_parse_group_file_message_with_bot_mention():
    parsed = parse_event_payload(
        {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {
                    "sender_type": "user",
                    "sender_id": {"open_id": "ou_test_user"},
                },
                "message": {
                    "message_id": "om_group_file",
                    "chat_id": "oc_group",
                    "chat_type": "group",
                    "message_type": "file",
                    "content": '{"file_key":"file_v2_xyz","file_name":"report.txt"}',
                    "mentions": [
                        {
                            "key": "@_user_1",
                            "id": {"open_id": BOT_OPEN_ID},
                            "name": "Access Assistant",
                        }
                    ],
                },
            },
        },
        options=FeishuParseOptions(bot_open_id=BOT_OPEN_ID),
    )
    assert isinstance(parsed, FeishuTextMessage)
    assert parsed.chat_type == "group"
    assert parsed.file_name == "report.txt"


def test_ignore_unsupported_message_type():
    parsed = parse_event_payload(
        {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_x"}},
                "message": {
                    "message_id": "om_img",
                    "chat_id": "oc_x",
                    "chat_type": "p2p",
                    "message_type": "image",
                    "content": "{}",
                },
            },
        }
    )
    assert isinstance(parsed, IgnoredEvent)
    assert "unsupported message_type=image" in parsed.reason


def test_parse_p2p_text_message():
    parsed = parse_event_payload(
        {
            "schema": "2.0",
            "header": {
                "event_id": "evt_123",
                "event_type": "im.message.receive_v1",
                "token": "verify-token",
            },
            "event": {
                "sender": {
                    "sender_type": "user",
                    "sender_id": {"open_id": "ou_test_user"},
                },
                "message": {
                    "message_id": "om_test",
                    "chat_id": "oc_test_chat",
                    "chat_type": "p2p",
                    "message_type": "text",
                    "content": "{\"text\":\"VIP等级\"}",
                },
            },
        }
    )
    assert isinstance(parsed, FeishuTextMessage)
    assert parsed.event_id == "evt_123"
    assert parsed.text == "VIP等级"
    assert parsed.open_id == "ou_test_user"
    assert parsed.chat_type == "p2p"
    assert dedupe_key(parsed) == "event:evt_123"


def test_parse_group_message_with_bot_mention():
    parsed = parse_event_payload(
        {
            "schema": "2.0",
            "header": {
                "event_id": "evt_group",
                "event_type": "im.message.receive_v1",
            },
            "event": {
                "sender": {
                    "sender_type": "user",
                    "sender_id": {"open_id": "ou_test_user"},
                },
                "message": {
                    "message_id": "om_group",
                    "chat_id": "oc_group",
                    "chat_type": "group",
                    "message_type": "text",
                    "content": "{\"text\":\"@_user_1 3599948273 登录失败\"}",
                    "mentions": [
                        {
                            "key": "@_user_1",
                            "id": {"open_id": BOT_OPEN_ID},
                            "name": "Access Assistant",
                        }
                    ],
                },
            },
        },
        options=FeishuParseOptions(bot_open_id=BOT_OPEN_ID),
    )
    assert isinstance(parsed, FeishuTextMessage)
    assert parsed.chat_type == "group"
    assert parsed.text == "3599948273 登录失败"


def test_group_text_without_mention_not_accepted():
    parsed = parse_event_payload(
        {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_x"}},
                "message": {
                    "message_id": "om_x",
                    "chat_id": "oc_group",
                    "chat_type": "group",
                    "message_type": "text",
                    "content": "{\"text\":\"hello\"}",
                },
            },
        },
        options=FeishuParseOptions(bot_open_id=BOT_OPEN_ID),
    )
    assert isinstance(parsed, FeishuTextMessage)
    assert not should_accept_group_message(
        parsed,
        bot_open_id=BOT_OPEN_ID,
        require_group_mention=True,
        group_file_without_mention=True,
    )


def test_group_file_without_mention_accepted():
    parsed = parse_event_payload(
        {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_x"}},
                "message": {
                    "message_id": "om_file",
                    "chat_id": "oc_group",
                    "chat_type": "group",
                    "message_type": "file",
                    "content": '{"file_key":"file_v2_abc","file_name":"notes.md"}',
                },
            },
        },
        options=FeishuParseOptions(bot_open_id=BOT_OPEN_ID),
    )
    assert isinstance(parsed, FeishuTextMessage)
    assert should_accept_group_message(
        parsed,
        bot_open_id=BOT_OPEN_ID,
        require_group_mention=True,
        group_file_without_mention=True,
    )


def test_group_text_with_file_keyword_without_mention_accepted():
    parsed = parse_event_payload(
        {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_x"}},
                "message": {
                    "message_id": "om_doc",
                    "chat_id": "oc_group",
                    "chat_type": "group",
                    "message_type": "text",
                    "content": "{\"text\":\"请分析这份文档\"}",
                },
            },
        },
        options=FeishuParseOptions(bot_open_id=BOT_OPEN_ID),
    )
    assert isinstance(parsed, FeishuTextMessage)
    keywords = frozenset({"文件", "文档", "附件", "报告"})
    assert should_accept_group_message(
        parsed,
        bot_open_id=BOT_OPEN_ID,
        require_group_mention=True,
        group_file_without_mention=True,
        file_intent_keywords=keywords,
    )


def test_group_text_with_pending_file_without_mention_accepted():
    parsed = parse_event_payload(
        {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_x"}},
                "message": {
                    "message_id": "om_text",
                    "chat_id": "oc_group",
                    "chat_type": "group",
                    "message_type": "text",
                    "content": "{\"text\":\"请总结\"}",
                },
            },
        },
        options=FeishuParseOptions(bot_open_id=BOT_OPEN_ID),
    )
    assert isinstance(parsed, FeishuTextMessage)
    pending = FeishuPendingStore()
    pending.set_file(
        "oc_group",
        "ou_x",
        PendingFile(
            file_name="notes.md",
            file_content="body",
            truncated=False,
            source_message_id="om_file",
        ),
    )
    assert should_accept_group_message(
        parsed,
        bot_open_id=BOT_OPEN_ID,
        require_group_mention=True,
        group_file_without_mention=True,
        pending_store=pending,
    )


def test_ignore_group_when_disabled():
    parsed = parse_event_payload(
        {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_x"}},
                "message": {
                    "message_id": "om_x",
                    "chat_id": "oc_group",
                    "chat_type": "group",
                    "message_type": "text",
                    "content": "{\"text\":\"@_user_1 hi\"}",
                    "mentions": [
                        {"key": "@_user_1", "id": {"open_id": BOT_OPEN_ID}, "name": "Bot"}
                    ],
                },
            },
        },
        options=FeishuParseOptions(group_enabled=False, bot_open_id=BOT_OPEN_ID),
    )
    assert isinstance(parsed, IgnoredEvent)
    assert "group chat disabled" in parsed.reason


def test_strip_mention_tokens():
    mentions = (
        {
            "key": "@_user_1",
            "id": {"open_id": BOT_OPEN_ID},
            "name": "Access Assistant",
        },
    )
    assert (
        strip_mention_tokens("@_user_1 @Access Assistant 你好", mentions)
        == "你好"
    )


def test_mentions_include_bot():
    mentions = ({"id": {"open_id": BOT_OPEN_ID}},)
    assert mentions_include_bot(mentions, BOT_OPEN_ID) is True
    assert mentions_include_bot(mentions, "ou_other") is False


def test_session_store_new_command():
    store = FeishuSessionStore()
    assert store.build_thread_id("oc_a", "ou_u") == "feishu:oc_a:ou_u"
    new_thread = store.reset("oc_a", "ou_u")
    assert new_thread == "feishu:oc_a:ou_u:s1"
    assert store.build_thread_id("oc_a", "ou_u") == "feishu:oc_a:ou_u:s1"


def test_deduper():
    deduper = EventDeduper(max_size=10, ttl_seconds=3600)
    assert deduper.is_duplicate("event:1") is False
    assert deduper.is_duplicate("event:1") is True


def test_commands():
    assert is_new_session_command("/new")
    assert is_new_session_command("新对话")
    assert is_help_command("/help")
    assert is_help_command("帮助")


def test_feishu_config_from_env(monkeypatch):
    monkeypatch.setenv("FEISHU_ENABLED", "true")
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret")
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "token")
    monkeypatch.setenv("FEISHU_ALLOWED_OPEN_IDS", "ou_a,ou_b")
    config = FeishuConfig.from_env()
    config.validate_runtime()
    assert config.enabled is True
    assert config.group_enabled is True
    assert config.is_sender_allowed("any_chat", "ou_a") is True
    assert config.is_sender_allowed("any_chat", "ou_x") is False
