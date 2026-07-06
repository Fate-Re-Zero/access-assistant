from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi.testclient import TestClient

from access_assistant.channels.feishu.config import FeishuConfig
from access_assistant.channels.feishu.handler import FeishuMessageHandler
from access_assistant.channels.feishu.webhook import create_feishu_router


class FakeAgent:
    def __init__(self) -> None:
        self.last_message = ""
        self.last_thread_id = ""
        self.invoke_count = 0

    def invoke(self, message: str, thread_id: str = "default") -> dict[str, Any]:
        self.invoke_count += 1
        self.last_message = message
        self.last_thread_id = thread_id
        return {"final_response": f"echo:{message[:120]}"}

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


class FakeClient:
    async def download_message_file(self, message_id: str, file_key: str) -> bytes:
        return b"file body content"


def test_handler_prepare_pending_file_rejects_pdf():
    handler = FeishuMessageHandler(
        _make_config(),
        client=FakeClient(),
        agent_provider=lambda: FakeAgent(),
    )
    from access_assistant.channels.feishu.events import FeishuTextMessage

    message = FeishuTextMessage(
        event_id="evt_1",
        message_id="om_1",
        chat_id="oc_1",
        open_id="ou_1",
        text="",
        message_type="file",
        file_key="file_v2_x",
        file_name="report.pdf",
    )

    async def run() -> tuple[Any, str | None]:
        return await handler._prepare_pending_file(message)

    pending_file, error = asyncio.run(run())
    assert pending_file is None
    assert error is not None
    assert "暂不支持" in error


def test_file_then_text_triggers_agent_once():
    agent = FakeAgent()
    replies: list[str] = []

    class RecordingClient(FakeClient):
        async def reply_text(self, message_id: str, text: str, **kwargs) -> None:
            replies.append(text)

    handler = FeishuMessageHandler(
        _make_config(),
        client=RecordingClient(),
        agent_provider=lambda: agent,
    )
    from access_assistant.channels.feishu.events import FeishuTextMessage

    file_message = FeishuTextMessage(
        event_id="evt_file",
        message_id="om_file",
        chat_id="oc_1",
        open_id="ou_1",
        text="",
        message_type="file",
        file_key="file_v2_a",
        file_name="demo.txt",
    )
    text_message = FeishuTextMessage(
        event_id="evt_text",
        message_id="om_text",
        chat_id="oc_1",
        open_id="ou_1",
        text="请总结",
        message_type="text",
    )

    async def run() -> None:
        await handler.process_text_message(file_message)
        await handler.process_text_message(text_message)

    asyncio.run(run())
    assert agent.invoke_count == 1
    assert "[用户上传文件: demo.txt]" in agent.last_message
    assert "file body content" in agent.last_message
    assert "请总结" in agent.last_message
    assert any("已收到文件" in reply for reply in replies)


def test_text_intent_then_file_triggers_agent_once():
    agent = FakeAgent()

    handler = FeishuMessageHandler(
        _make_config(),
        client=FakeClient(),
        agent_provider=lambda: agent,
    )
    from access_assistant.channels.feishu.events import FeishuTextMessage

    text_message = FeishuTextMessage(
        event_id="evt_q",
        message_id="om_q",
        chat_id="oc_2",
        open_id="ou_2",
        text="请帮我分析这个文档",
        message_type="text",
    )
    file_message = FeishuTextMessage(
        event_id="evt_f",
        message_id="om_f",
        chat_id="oc_2",
        open_id="ou_2",
        text="",
        message_type="file",
        file_key="file_v2_b",
        file_name="notes.md",
    )

    async def run() -> None:
        await handler.process_text_message(text_message)
        await handler.process_text_message(file_message)

    asyncio.run(run())
    assert agent.invoke_count == 1
    assert "请帮我分析这个文档" in agent.last_message
    assert "file body content" in agent.last_message


def test_webhook_file_does_not_invoke_agent_immediately(monkeypatch):
    config = _make_config()
    agent = FakeAgent()

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_feishu_router(config, lambda: agent))
    client = TestClient(app)

    async def fake_download(_self, message_id: str, file_key: str) -> bytes:
        return b"file body"

    monkeypatch.setattr(
        "access_assistant.channels.feishu.client.FeishuClient.download_message_file",
        fake_download,
    )

    replies: list[str] = []

    async def fake_reply(_self, message_id: str, text: str, **kwargs) -> None:
        replies.append(text)

    monkeypatch.setattr(
        "access_assistant.channels.feishu.handler.FeishuClient.reply_text",
        fake_reply,
    )
    monkeypatch.setattr(
        "access_assistant.channels.feishu.handler.FeishuClient.send_text_to_chat",
        lambda *_args, **_kwargs: None,
    )

    response = client.post(
        "/feishu/webhook",
        json={
            "schema": "2.0",
            "header": {
                "event_id": "evt_file_webhook",
                "event_type": "im.message.receive_v1",
                "token": "verify-token",
            },
            "event": {
                "sender": {
                    "sender_type": "user",
                    "sender_id": {"open_id": "ou_test_user"},
                },
                "message": {
                    "message_id": "om_file_webhook",
                    "chat_id": "oc_test_chat",
                    "chat_type": "p2p",
                    "message_type": "file",
                    "content": '{"file_key":"file_v2_demo","file_name":"demo.txt"}',
                },
            },
        },
    )
    assert response.status_code == 200
    assert response.json() == {"code": 0, "msg": "accepted"}

    deadline = time.time() + 2.0
    while time.time() < deadline and not replies:
        time.sleep(0.05)

    assert agent.invoke_count == 0
    assert any("已收到文件" in reply for reply in replies)
