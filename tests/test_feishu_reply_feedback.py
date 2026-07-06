from __future__ import annotations

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_pkg = types.ModuleType("access_assistant")
_pkg.__path__ = [str(ROOT / "access_assistant")]
sys.modules.setdefault("access_assistant", _pkg)

mcp_adapters = types.ModuleType("langchain_mcp_adapters")
mcp_client = types.ModuleType("langchain_mcp_adapters.client")
mcp_client.MultiServerMCPClient = object  # type: ignore[attr-defined]
mcp_adapters.client = mcp_client
sys.modules.setdefault("langchain_mcp_adapters", mcp_adapters)
sys.modules.setdefault("langchain_mcp_adapters.client", mcp_client)

from fastapi.testclient import TestClient

from access_assistant.channels.feishu.config import FeishuConfig
from access_assistant.channels.feishu.reply_feedback import (
    FEEDBACK_HELPFUL,
    FEEDBACK_THANKS_TEXT,
    FEEDBACK_UNHELPFUL,
    build_feedback_callback_response,
    build_interactive_card,
    get_reply_card_content,
    parse_reply_feedback_callback,
    store_reply_card_content,
)
from access_assistant.channels.feishu.webhook import create_feishu_router


def _make_config(**overrides) -> FeishuConfig:
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
        "use_lark_md": True,
        "use_interactive_card": True,
        "reply_feedback_enabled": True,
        "reply_feedback_use_callback": True,
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
        "bot_open_id": "ou_bot",
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


def test_build_interactive_card_includes_feedback_buttons():
    card = build_interactive_card(
        "hello",
        include_feedback=True,
        feedback_use_callback=True,
    )
    elements = card["body"]["elements"]
    assert elements[0]["tag"] == "markdown"
    feedback_row = elements[1]
    assert feedback_row["tag"] == "column_set"
    assert feedback_row["horizontal_align"] == "center"
    actions = feedback_row["columns"][0]["elements"] + feedback_row["columns"][1]["elements"]
    assert actions[0]["text"]["content"] == "👍 有帮助"
    assert actions[1]["text"]["content"] == "👎 无帮助"
    assert actions[0]["type"] == "default"
    assert actions[1]["type"] == "default"
    assert actions[0]["behaviors"][0]["value"]["feedback"] == FEEDBACK_HELPFUL


def test_build_interactive_card_without_callback_still_renders_buttons():
    card = build_interactive_card(
        "hello",
        include_feedback=True,
        feedback_use_callback=False,
    )
    feedback_row = card["body"]["elements"][1]
    assert feedback_row["tag"] == "column_set"
    assert feedback_row["horizontal_align"] == "center"
    helpful = feedback_row["columns"][0]["elements"][0]
    unhelpful = feedback_row["columns"][1]["elements"][0]
    assert helpful["tag"] == "button"
    assert unhelpful["tag"] == "button"
    assert "behaviors" not in helpful
    assert "behaviors" not in unhelpful


def test_build_feedback_callback_response_marks_selected_button():
    response = build_feedback_callback_response(
        FEEDBACK_UNHELPFUL,
        original_lark_md="answer body",
    )
    assert response["toast"]["content"] == FEEDBACK_THANKS_TEXT
    card = response["card"]["data"]
    elements = card["body"]["elements"]
    assert elements[0]["content"] == "answer body"
    assert FEEDBACK_THANKS_TEXT in elements[1]["content"]
    feedback_row = elements[2]
    assert feedback_row["tag"] == "column_set"
    assert feedback_row["horizontal_align"] == "center"
    actions = feedback_row["columns"][0]["elements"] + feedback_row["columns"][1]["elements"]
    assert actions[0]["disabled"] is False
    assert actions[1]["disabled"] is True
    assert actions[1]["type"] == "primary"


def test_parse_reply_feedback_callback():
    payload = {
        "header": {"event_type": "card.action.trigger"},
        "event": {
            "action": {
                "value": {"action": "reply_feedback", "feedback": "helpful"},
            },
            "context": {"open_message_id": "om_123"},
        },
    }
    assert parse_reply_feedback_callback(payload) == FEEDBACK_HELPFUL


def test_reply_content_cache_roundtrip():
    store_reply_card_content("om_cached", "cached answer")
    assert get_reply_card_content("om_cached") == "cached answer"


def test_card_feedback_webhook_returns_updated_card():
    store_reply_card_content("om_feedback", "机器人回复内容")

    class FakeAgent:
        def invoke(self, message: str, thread_id: str = "default") -> dict:
            return {"final_response": message}

        def invoke_auth(self, message: str, thread_id: str = "default") -> dict:
            return {"final_response": message}

        def get_last_response(self, result: dict) -> str:
            return str(result.get("final_response", ""))

    from fastapi import FastAPI

    agent = FakeAgent()
    app = FastAPI()
    app.include_router(create_feishu_router(_make_config(), lambda: agent))
    client = TestClient(app)
    payload = {
        "header": {
            "event_type": "card.action.trigger",
            "token": "verify-token",
        },
        "event": {
            "action": {
                "value": {"action": "reply_feedback", "feedback": "helpful"},
            },
            "context": {"open_message_id": "om_feedback"},
        },
    }
    response = client.post("/feishu/card/callback", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["toast"]["content"] == FEEDBACK_THANKS_TEXT
    card = body["card"]["data"]
    assert card["body"]["elements"][0]["content"] == "机器人回复内容"
    feedback_row = card["body"]["elements"][2]
    assert feedback_row["tag"] == "column_set"
    actions = feedback_row["columns"][0]["elements"] + feedback_row["columns"][1]["elements"]
    assert actions[0]["disabled"] is True
    assert actions[0]["type"] == "primary"


def test_card_callback_url_verification():
    from fastapi import FastAPI

    class FakeAgent:
        def invoke(self, message: str, thread_id: str = "default") -> dict:
            return {"final_response": message}

        def get_last_response(self, result: dict) -> str:
            return str(result.get("final_response", ""))

    app = FastAPI()
    app.include_router(create_feishu_router(_make_config(), lambda: FakeAgent()))
    client = TestClient(app)
    response = client.post(
        "/feishu/card/callback",
        json={
            "challenge": "card-callback-challenge",
            "token": "verify-token",
            "type": "url_verification",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"challenge": "card-callback-challenge"}


def test_card_callback_url_verification_schema_v2():
    from fastapi import FastAPI

    class FakeAgent:
        def invoke(self, message: str, thread_id: str = "default") -> dict:
            return {"final_response": message}

        def get_last_response(self, result: dict) -> str:
            return str(result.get("final_response", ""))

    app = FastAPI()
    app.include_router(create_feishu_router(_make_config(), lambda: FakeAgent()))
    client = TestClient(app)
    response = client.post(
        "/feishu/card/callback",
        json={
            "schema": "2.0",
            "header": {
                "event_type": "url_verification",
                "token": "verify-token",
            },
            "event": {
                "challenge": "schema-v2-challenge",
            },
        },
    )
    assert response.status_code == 200
    assert response.json() == {"challenge": "schema-v2-challenge"}


def test_build_payload_includes_feedback_on_last_chunk_only(monkeypatch):
    import asyncio

    from access_assistant.channels.feishu.client import FeishuClient

    client = FeishuClient(_make_config())
    captured: list[dict] = []

    async def fake_token() -> str:
        return "token"

    async def fake_post(token: str, url: str, payload: dict) -> dict:
        captured.append(payload)
        return {"code": 0, "data": {"message_id": f"om_{len(captured)}"}}

    monkeypatch.setattr(client, "_get_tenant_access_token", fake_token)
    monkeypatch.setattr(client, "_post_json", fake_post)

    async def run() -> None:
        await client.reply_text("message_id", "x" * 3000)

    asyncio.run(run())
    assert len(captured) == 2
    first_card = json.loads(captured[0]["content"])
    second_card = json.loads(captured[1]["content"])
    assert "column_set" not in {item.get("tag") for item in first_card["body"]["elements"]}
    assert "column_set" in {item.get("tag") for item in second_card["body"]["elements"]}


def test_processing_message_uses_plain_text_without_feedback(monkeypatch):
    import asyncio

    from access_assistant.channels.feishu.client import FeishuClient

    client = FeishuClient(_make_config(show_processing_message=True))
    captured: list[dict] = []

    async def fake_token() -> str:
        return "token"

    async def fake_post(token: str, url: str, payload: dict) -> dict:
        captured.append(payload)
        return {"code": 0, "data": {"message_id": "om_processing"}}

    monkeypatch.setattr(client, "_get_tenant_access_token", fake_token)
    monkeypatch.setattr(client, "_post_json", fake_post)

    async def run() -> None:
        await client.reply_text("message_id", "正在处理，请稍候…", force_plain=True)

    asyncio.run(run())
    assert len(captured) == 1
    assert captured[0]["msg_type"] == "text"
    content = json.loads(str(captured[0]["content"]))
    assert content["text"] == "正在处理，请稍候…"
