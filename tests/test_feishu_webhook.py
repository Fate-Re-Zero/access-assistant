from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from access_assistant.channels.feishu.config import FeishuAuthBotConfig, FeishuConfig
from access_assistant.channels.feishu.webhook import create_feishu_router


class FakeAgent:
    def __init__(self) -> None:
        self.last_message = ""
        self.last_thread_id = ""
        self.auth_last_message = ""
        self.auth_last_thread_id = ""

    def invoke(self, message: str, thread_id: str = "default") -> dict[str, Any]:
        self.last_message = message
        self.last_thread_id = thread_id
        return {"final_response": f"echo:{message}"}

    def invoke_auth(self, message: str, thread_id: str = "default") -> dict[str, Any]:
        self.auth_last_message = message
        self.auth_last_thread_id = thread_id
        return {"final_response": f"auth:{message}"}

    def get_last_response(self, result: dict[str, Any]) -> str:
        return str(result.get("final_response", ""))


def _make_config(**overrides: Any) -> FeishuConfig:
    defaults = {
        "enabled": True,
        "app_id": "cli_test",
        "app_secret": "secret",
        "verification_token": "verify-token",
        "encrypt_key": None,
        "api_base": "https://open.feishu.cn",
        "agent_timeout_seconds": 30.0,
        "show_processing_message": False,
        "processing_text": "正在处理，请稍候…",
        "allowed_chat_ids": frozenset(),
        "allowed_open_ids": frozenset(),
        "auth_allowed_chat_ids": frozenset(),
        "dedupe_ttl_seconds": 86400.0,
        "dedupe_max_size": 10000,
        "use_lark_md": False,
        "use_interactive_card": False,
        "reply_feedback_enabled": False,
        "reply_feedback_use_callback": False,
        "show_progress_updates": False,
        "progress_min_interval_seconds": 3.0,
        "text_chunk_size": 3800,
        "persistence_enabled": False,
        "data_dir": None,
        "audit_enabled": False,
        "audit_max_content_length": 2000,
        "sso_enabled": False,
        "sso_allowed_email_domains": frozenset(),
        "sso_cache_ttl_seconds": 3600.0,
        "group_enabled": True,
        "bot_open_id": "ou_bot_app",
        "require_group_mention": True,
        "group_file_without_mention": True,
        "file_inbound_enabled": True,
        "file_max_bytes": 512000,
        "file_max_prompt_chars": 80000,
        "file_allowed_extensions": frozenset({".txt", ".md", ".markdown"}),
        "file_bidirectional_enabled": True,
        "file_pending_ttl_seconds": 600.0,
        "file_pending_max_size": 5000,
        "file_intent_keywords": frozenset({"文件", "文档", "附件", "报告"}),
        "file_intent_llm_enabled": False,
        "file_intent_llm_timeout_seconds": 10.0,
    }
    defaults.update(overrides)
    return FeishuConfig(**defaults)


def _make_auth_bot_config(**overrides: Any) -> FeishuAuthBotConfig:
    defaults = {
        "enabled": True,
        "app_id": "cli_auth_test",
        "app_secret": "auth_secret",
        "verification_token": "auth-verify-token",
        "encrypt_key": None,
        "bot_open_id": "ou_bot_app",
        "allowed_chat_ids": frozenset(),
        "data_dir": None,
        "p2p_enabled": True,
        "show_processing_message": None,
        "show_progress_updates": None,
        "processing_text": None,
    }
    defaults.update(overrides)
    return FeishuAuthBotConfig(**defaults)


def _include_feishu_router(
    app: Any,
    config: FeishuConfig,
    agent: FakeAgent,
    auth_bot_config: FeishuAuthBotConfig | None = None,
) -> None:
    app.include_router(
        create_feishu_router(
            config,
            lambda: agent,
            auth_bot_config=auth_bot_config,
        )
    )


@pytest.fixture
def feishu_client():
    config = _make_config()
    agent = FakeAgent()

    from fastapi import FastAPI

    app = FastAPI()
    _include_feishu_router(app, config, agent)
    return TestClient(app), agent


def test_webhook_url_verification(feishu_client):
    client, _agent = feishu_client
    response = client.post(
        "/feishu/webhook",
        json={
            "challenge": "challenge-value",
            "token": "verify-token",
            "type": "url_verification",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"challenge": "challenge-value"}


def test_webhook_accepts_p2p_message(feishu_client, monkeypatch):
    client, agent = feishu_client
    called = {"reply": False, "send": False}

    async def fake_reply(_self, message_id: str, text: str, **kwargs) -> None:
        called["reply"] = True
        assert message_id == "om_test"
        assert text == "echo:VIP等级"

    async def fake_send(_self, chat_id: str, text: str) -> None:
        called["send"] = True

    monkeypatch.setattr(
        "access_assistant.channels.feishu.handler.FeishuClient.reply_text",
        fake_reply,
    )
    monkeypatch.setattr(
        "access_assistant.channels.feishu.handler.FeishuClient.send_text_to_chat",
        fake_send,
    )

    response = client.post(
        "/feishu/webhook",
        json={
            "schema": "2.0",
            "header": {
                "event_id": "evt_1",
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
        },
    )
    assert response.status_code == 200
    assert response.json()["code"] == 0
    assert agent.last_message == "VIP等级"
    assert agent.last_thread_id == "feishu:oc_test_chat:ou_test_user"
    assert called["reply"] is True


def _auth_webhook_client(
    agent: FakeAgent | None = None,
    *,
    auth_bot_overrides: dict[str, Any] | None = None,
) -> tuple[TestClient, FakeAgent]:
    agent = agent or FakeAgent()
    config = _make_config()
    auth_bot = _make_auth_bot_config(**(auth_bot_overrides or {}))

    from fastapi import FastAPI

    app = FastAPI()
    _include_feishu_router(app, config, agent, auth_bot_config=auth_bot)
    return TestClient(app), agent


def test_auth_webhook_requires_auth_bot_config(feishu_client):
    client, _agent = feishu_client
    response = client.post(
        "/feishu/auth/webhook",
        json={
            "type": "url_verification",
            "challenge": "ping",
            "token": "verify-token",
        },
    )
    assert response.status_code == 503


def test_auth_webhook_routes_to_auth_agent(monkeypatch):
    client, agent = _auth_webhook_client()
    replies: list[str] = []

    async def fake_reply(_self, message_id: str, text: str, **kwargs) -> None:
        replies.append(text)

    monkeypatch.setattr(
        "access_assistant.channels.feishu.handler.FeishuClient.reply_text",
        fake_reply,
    )

    response = client.post(
        "/feishu/auth/webhook",
        json={
            "schema": "2.0",
            "header": {
                "event_id": "evt_auth_1",
                "event_type": "im.message.receive_v1",
                "token": "auth-verify-token",
            },
            "event": {
                "sender": {
                    "sender_type": "user",
                    "sender_id": {"open_id": "ou_test_user"},
                },
                "message": {
                    "message_id": "om_auth_test",
                    "chat_id": "oc_test_chat",
                    "chat_type": "p2p",
                    "message_type": "text",
                    "content": "{\"text\":\"3599948273 登录失败\"}",
                },
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["code"] == 0
    assert agent.last_message == ""
    assert "3599948273 登录失败" in agent.auth_last_message
    assert "增加实名认证次数上限" in agent.auth_last_message
    assert agent.auth_last_thread_id == "feishu-auth:oc_test_chat:ou_test_user"
    assert len(replies) == 1
    assert "3599948273 登录失败" in replies[0]


def test_auth_webhook_rejects_group_not_in_whitelist(monkeypatch):
    client, agent = _auth_webhook_client(
        auth_bot_overrides={"allowed_chat_ids": frozenset({"oc_allowed_group"})},
    )
    replies: list[str] = []

    async def fake_reply(_self, message_id: str, text: str, **kwargs) -> None:
        replies.append(text)

    monkeypatch.setattr(
        "access_assistant.channels.feishu.handler.FeishuClient.reply_text",
        fake_reply,
    )

    response = client.post(
        "/feishu/auth/webhook",
        json={
            "schema": "2.0",
            "header": {
                "event_id": "evt_auth_blocked",
                "event_type": "im.message.receive_v1",
                "token": "auth-verify-token",
            },
            "event": {
                "sender": {
                    "sender_type": "user",
                    "sender_id": {"open_id": "ou_test_user"},
                },
                "message": {
                    "message_id": "om_auth_blocked",
                    "chat_id": "oc_other_group",
                    "chat_type": "group",
                    "message_type": "text",
                    "content": "{\"text\":\"@_user_1 登录失败\"}",
                    "mentions": [
                        {
                            "key": "@_user_1",
                            "id": {"open_id": "ou_bot_app"},
                            "name": "Access Assistant",
                        }
                    ],
                },
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["msg"] == "ignored"
    assert agent.auth_last_message == ""
    assert replies == ["当前群聊无法使用该机器人的功能。"]


def test_auth_webhook_rejects_p2p_when_disabled(monkeypatch):
    client, agent = _auth_webhook_client(
        auth_bot_overrides={"p2p_enabled": False},
    )
    replies: list[str] = []

    async def fake_reply(_self, message_id: str, text: str, **kwargs) -> None:
        replies.append(text)

    monkeypatch.setattr(
        "access_assistant.channels.feishu.handler.FeishuClient.reply_text",
        fake_reply,
    )

    response = client.post(
        "/feishu/auth/webhook",
        json={
            "schema": "2.0",
            "header": {
                "event_id": "evt_auth_p2p_denied",
                "event_type": "im.message.receive_v1",
                "token": "auth-verify-token",
            },
            "event": {
                "sender": {
                    "sender_type": "user",
                    "sender_id": {"open_id": "ou_test_user"},
                },
                "message": {
                    "message_id": "om_auth_p2p_denied",
                    "chat_id": "oc_p2p_chat",
                    "chat_type": "p2p",
                    "message_type": "text",
                    "content": "{\"text\":\"18877179115 操作上限 增加次数\"}",
                },
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["msg"] == "ignored"
    assert agent.auth_last_message == ""
    assert replies == ["当前机器人仅支持在指定群聊中使用，暂不支持私聊。"]


def test_auth_webhook_allows_p2p_when_whitelist_configured(monkeypatch):
    client, agent = _auth_webhook_client(
        auth_bot_overrides={"allowed_chat_ids": frozenset({"oc_allowed_group"})},
    )
    replies: list[str] = []

    async def fake_reply(_self, message_id: str, text: str, **kwargs) -> None:
        replies.append(text)

    monkeypatch.setattr(
        "access_assistant.channels.feishu.handler.FeishuClient.reply_text",
        fake_reply,
    )

    response = client.post(
        "/feishu/auth/webhook",
        json={
            "schema": "2.0",
            "header": {
                "event_id": "evt_auth_p2p",
                "event_type": "im.message.receive_v1",
                "token": "auth-verify-token",
            },
            "event": {
                "sender": {
                    "sender_type": "user",
                    "sender_id": {"open_id": "ou_test_user"},
                },
                "message": {
                    "message_id": "om_auth_p2p",
                    "chat_id": "oc_p2p_chat",
                    "chat_type": "p2p",
                    "message_type": "text",
                    "content": "{\"text\":\"3599948273 登录失败\"}",
                },
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["msg"] == "accepted"
    assert "3599948273 登录失败" in agent.auth_last_message
    assert "增加实名认证次数上限" in agent.auth_last_message
    assert len(replies) == 1
    assert "3599948273 登录失败" in replies[0]


def test_auth_webhook_accepts_whitelisted_group(monkeypatch):
    client, agent = _auth_webhook_client(
        auth_bot_overrides={"allowed_chat_ids": frozenset({"oc_allowed_group"})},
    )
    replies: list[str] = []

    async def fake_reply(_self, message_id: str, text: str, **kwargs) -> None:
        replies.append(text)

    monkeypatch.setattr(
        "access_assistant.channels.feishu.handler.FeishuClient.reply_text",
        fake_reply,
    )

    response = client.post(
        "/feishu/auth/webhook",
        json={
            "schema": "2.0",
            "header": {
                "event_id": "evt_auth_allowed",
                "event_type": "im.message.receive_v1",
                "token": "auth-verify-token",
            },
            "event": {
                "sender": {
                    "sender_type": "user",
                    "sender_id": {"open_id": "ou_test_user"},
                },
                "message": {
                    "message_id": "om_auth_allowed",
                    "chat_id": "oc_allowed_group",
                    "chat_type": "group",
                    "message_type": "text",
                    "content": "{\"text\":\"@_user_1 登录失败\"}",
                    "mentions": [
                        {
                            "key": "@_user_1",
                            "id": {"open_id": "ou_bot_app"},
                            "name": "Access Assistant",
                        }
                    ],
                },
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["msg"] == "accepted"
    assert "登录失败" in agent.auth_last_message
    assert "增加实名认证次数上限" in agent.auth_last_message
    assert len(replies) == 1
    assert "登录失败" in replies[0]


def test_webhook_accepts_group_at_message(feishu_client, monkeypatch):
    client, agent = feishu_client
    replies: list[str] = []

    async def fake_reply(_self, message_id: str, text: str, **kwargs) -> None:
        replies.append(text)

    async def fake_send(_self, chat_id: str, text: str) -> None:
        replies.append(text)

    monkeypatch.setattr(
        "access_assistant.channels.feishu.handler.FeishuClient.reply_text",
        fake_reply,
    )
    monkeypatch.setattr(
        "access_assistant.channels.feishu.handler.FeishuClient.send_text_to_chat",
        fake_send,
    )

    response = client.post(
        "/feishu/webhook",
        json={
            "schema": "2.0",
            "header": {
                "event_id": "evt_group",
                "event_type": "im.message.receive_v1",
                "token": "verify-token",
            },
            "event": {
                "sender": {
                    "sender_type": "user",
                    "sender_id": {"open_id": "ou_test_user"},
                },
                "message": {
                    "message_id": "om_group",
                    "chat_id": "oc_group_chat",
                    "chat_type": "group",
                    "message_type": "text",
                    "content": "{\"text\":\"@_user_1 登录失败\"}",
                    "mentions": [
                        {
                            "key": "@_user_1",
                            "id": {"open_id": "ou_bot_app"},
                            "name": "Access Assistant",
                        }
                    ],
                },
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["msg"] == "accepted"
    assert agent.last_message == "登录失败"
    assert agent.last_thread_id == "feishu:oc_group_chat:ou_test_user"
    assert replies


def test_webhook_accepts_group_file_without_mention(feishu_client, monkeypatch):
    client, agent = feishu_client
    replies: list[str] = []

    async def fake_reply(_self, message_id: str, text: str, **kwargs) -> None:
        replies.append(text)

    async def fake_download(_self, message_id: str, file_key: str) -> bytes:
        assert message_id == "om_group_file"
        assert file_key == "file_v2_abc"
        return b"# notes\nhello"

    monkeypatch.setattr(
        "access_assistant.channels.feishu.handler.FeishuClient.reply_text",
        fake_reply,
    )
    monkeypatch.setattr(
        "access_assistant.channels.feishu.handler.FeishuClient.download_message_file",
        fake_download,
    )

    response = client.post(
        "/feishu/webhook",
        json={
            "schema": "2.0",
            "header": {
                "event_id": "evt_group_file",
                "event_type": "im.message.receive_v1",
                "token": "verify-token",
            },
            "event": {
                "sender": {
                    "sender_type": "user",
                    "sender_id": {"open_id": "ou_test_user"},
                },
                "message": {
                    "message_id": "om_group_file",
                    "chat_id": "oc_group_chat",
                    "chat_type": "group",
                    "message_type": "file",
                    "content": '{"file_key":"file_v2_abc","file_name":"notes.md"}',
                },
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["msg"] == "accepted"
    assert agent.last_message == ""
    assert replies
    assert "已收到文件" in replies[0]
    assert "notes.md" in replies[0]


def test_webhook_ignores_group_text_without_mention(feishu_client, monkeypatch):
    client, agent = feishu_client
    replies: list[str] = []

    async def fake_reply(_self, message_id: str, text: str, **kwargs) -> None:
        replies.append(text)

    monkeypatch.setattr(
        "access_assistant.channels.feishu.handler.FeishuClient.reply_text",
        fake_reply,
    )

    response = client.post(
        "/feishu/webhook",
        json={
            "schema": "2.0",
            "header": {
                "event_id": "evt_group_text",
                "event_type": "im.message.receive_v1",
                "token": "verify-token",
            },
            "event": {
                "sender": {
                    "sender_type": "user",
                    "sender_id": {"open_id": "ou_test_user"},
                },
                "message": {
                    "message_id": "om_group_text",
                    "chat_id": "oc_group_chat",
                    "chat_type": "group",
                    "message_type": "text",
                    "content": '{"text":"hello without mention"}',
                },
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["msg"] == "ignored"
    assert agent.last_message == ""
    assert not replies


def test_webhook_dedupe(feishu_client, monkeypatch):
    client, _agent = feishu_client

    async def fake_reply(_self, message_id: str, text: str) -> None:
        return None

    monkeypatch.setattr(
        "access_assistant.channels.feishu.handler.FeishuClient.reply_text",
        fake_reply,
    )

    payload = {
        "schema": "2.0",
        "header": {
            "event_id": "evt_dup",
            "event_type": "im.message.receive_v1",
            "token": "verify-token",
        },
        "event": {
            "sender": {
                "sender_type": "user",
                "sender_id": {"open_id": "ou_test_user"},
            },
            "message": {
                "message_id": "om_dup",
                "chat_id": "oc_test_chat",
                "chat_type": "p2p",
                "message_type": "text",
                "content": "{\"text\":\"hello\"}",
            },
        },
    }
    first = client.post("/feishu/webhook", json=payload)
    second = client.post("/feishu/webhook", json=payload)
    assert first.json()["msg"] == "accepted"
    assert second.json()["msg"] == "duplicate"


def test_webhook_new_session_command(feishu_client, monkeypatch):
    client, agent = feishu_client
    replies: list[str] = []

    async def fake_reply(_self, message_id: str, text: str, **kwargs) -> None:
        replies.append(text)

    monkeypatch.setattr(
        "access_assistant.channels.feishu.handler.FeishuClient.reply_text",
        fake_reply,
    )

    response = client.post(
        "/feishu/webhook",
        json={
            "schema": "2.0",
            "header": {
                "event_id": "evt_new",
                "event_type": "im.message.receive_v1",
                "token": "verify-token",
            },
            "event": {
                "sender": {
                    "sender_type": "user",
                    "sender_id": {"open_id": "ou_test_user"},
                },
                "message": {
                    "message_id": "om_new",
                    "chat_id": "oc_test_chat",
                    "chat_type": "p2p",
                    "message_type": "text",
                    "content": "{\"text\":\"/new\"}",
                },
            },
        },
    )
    assert response.status_code == 200
    assert agent.last_message == ""
    assert "新对话" in replies[0]
