from __future__ import annotations

import sqlite3
from pathlib import Path

import asyncio
import pytest

from access_assistant.channels.feishu.audit import FeishuAuditLogger, redact_audit_content
from access_assistant.channels.feishu.config import FeishuConfig
from access_assistant.channels.feishu.identity import FeishuIdentityService, FeishuUserIdentity
from access_assistant.channels.feishu.session import FeishuSessionStore
from access_assistant.channels.feishu.storage import FeishuStorage


@pytest.fixture
def storage(tmp_path: Path) -> FeishuStorage:
    return FeishuStorage(tmp_path / "feishu.sqlite")


def test_session_persistence_survives_restart(storage: FeishuStorage):
    store = FeishuSessionStore(storage=storage)
    assert store.build_thread_id("oc_chat", "ou_user") == "feishu:oc_chat:ou_user"

    thread_id = store.reset("oc_chat", "ou_user")
    assert thread_id == "feishu:oc_chat:ou_user:s1"

    reloaded = FeishuSessionStore(storage=storage)
    assert reloaded.build_thread_id("oc_chat", "ou_user") == "feishu:oc_chat:ou_user:s1"


def test_audit_logger_persists_records(storage: FeishuStorage):
    audit = FeishuAuditLogger(storage, max_content_length=100)
    audit.log(
        direction="inbound",
        chat_id="oc_chat",
        open_id="ou_user",
        status="accepted",
        content="订单 13812345678 未到账",
        thread_id="feishu:oc_chat:ou_user",
        user_name="Alice",
        user_email="alice@example.com",
    )

    with storage._lock, storage._connect() as conn:
        row = conn.execute("SELECT content, user_name FROM feishu_audit").fetchone()

    assert row is not None
    assert row["user_name"] == "Alice"
    assert "[手机号已脱敏]" in row["content"]


def test_redact_audit_content_truncates():
    text = "a" * 300
    result = redact_audit_content(text, max_length=50)
    assert len(result) <= 50
    assert result.endswith("…")


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


class FakeClient:
    async def get_user_by_open_id(self, open_id: str) -> dict:
        return {
            "user": {
                "name": "Alice",
                "email": "alice@example.com",
                "enterprise_email": "alice@corp.com",
                "status": {"is_resigned": False, "is_frozen": False},
            }
        }


def test_identity_service_allows_active_user():
    config = _make_config(
        sso_enabled=True,
        sso_allowed_email_domains=frozenset({"corp.com"}),
    )
    service = FeishuIdentityService(config, FakeClient())  # type: ignore[arg-type]

    async def run() -> tuple[bool, FeishuUserIdentity | None, str]:
        return await service.verify("ou_user")

    allowed, identity, reason = asyncio.run(run())
    assert allowed is True
    assert identity is not None
    assert identity.name == "Alice"
    assert reason == ""


def test_identity_service_blocks_resigned_user():
    class ResignedClient(FakeClient):
        async def get_user_by_open_id(self, open_id: str) -> dict:
            payload = await super().get_user_by_open_id(open_id)
            payload["user"]["status"]["is_resigned"] = True
            return payload

    config = _make_config(sso_enabled=True)
    service = FeishuIdentityService(config, ResignedClient())  # type: ignore[arg-type]

    async def run() -> tuple[bool, FeishuUserIdentity | None, str]:
        return await service.verify("ou_user")

    allowed, _identity, reason = asyncio.run(run())
    assert allowed is False
    assert "离职" in reason
