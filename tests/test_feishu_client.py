from __future__ import annotations

import asyncio
import json

import pytest

from access_assistant.channels.feishu.client import FeishuClient, resolve_receive_id_type
from access_assistant.channels.feishu.config import FeishuConfig


def _make_config(**overrides) -> FeishuConfig:
    defaults = {
        "enabled": True,
        "app_id": "cli_test",
        "app_secret": "secret",
        "verification_token": "token",
        "encrypt_key": None,
        "api_base": "https://open.feishu.cn",
        "agent_timeout_seconds": 30.0,
        "show_processing_message": False,
        "processing_text": "正在处理…",
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
        "bot_open_id": "",
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


def test_resolve_receive_id_type():
    assert resolve_receive_id_type("oc_group") == "chat_id"
    assert resolve_receive_id_type("ou_user") == "open_id"
    assert resolve_receive_id_type("on_union") == "union_id"


def test_download_message_file(monkeypatch):
    client = FeishuClient(_make_config())
    captured: dict[str, str] = {}

    async def fake_token() -> str:
        return "token"

    class FakeResponse:
        status_code = 200
        content = b"hello file"

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url: str, headers: dict[str, str]):
            captured["url"] = url
            captured["auth"] = headers.get("Authorization", "")
            return FakeResponse()

    monkeypatch.setattr(client, "_get_tenant_access_token", fake_token)
    monkeypatch.setattr("access_assistant.channels.feishu.client.httpx.AsyncClient", FakeAsyncClient)

    async def run() -> bytes:
        return await client.download_message_file("om_123", "file_v2_abc")

    data = asyncio.run(run())
    assert data == b"hello file"
    assert captured["url"] == (
        "https://open.feishu.cn/open-apis/im/v1/messages/om_123/resources/file_v2_abc?type=file"
    )
    assert captured["auth"] == "Bearer token"


def test_deliver_to_chat_puts_receive_id_in_body(monkeypatch):
    client = FeishuClient(_make_config())
    captured: dict[str, object] = {}

    async def fake_token() -> str:
        return "token"

    async def fake_post(token: str, url: str, payload: dict) -> None:
        captured["token"] = token
        captured["url"] = url
        captured["payload"] = payload

    monkeypatch.setattr(client, "_get_tenant_access_token", fake_token)
    monkeypatch.setattr(client, "_post_json", fake_post)

    async def run() -> None:
        await client.send_text_to_chat("oc_e0889e7e7cc482ca4cb5a5391626fb5a", "hello")

    asyncio.run(run())

    assert captured["url"] == (
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    )
    payload = captured["payload"]
    assert payload["receive_id"] == "oc_e0889e7e7cc482ca4cb5a5391626fb5a"
    assert payload["msg_type"] == "text"
    content = json.loads(str(payload["content"]))
    assert content["text"] == "hello"
