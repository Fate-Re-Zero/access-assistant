from access_assistant.channels.feishu.config import FeishuAuthBotConfig, FeishuConfig


def _minimal_config(**overrides):
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
        "file_allowed_extensions": frozenset({".txt", ".md"}),
        "file_bidirectional_enabled": True,
        "file_pending_ttl_seconds": 600.0,
        "file_pending_max_size": 5000,
        "file_intent_keywords": frozenset({"文件"}),
        "file_intent_llm_enabled": False,
        "file_intent_llm_timeout_seconds": 10.0,
    }
    defaults.update(overrides)
    return FeishuConfig(**defaults)


def test_auth_webhook_chat_allowed_when_whitelist_empty() -> None:
    config = _minimal_config()
    assert config.is_auth_webhook_chat_allowed("oc_any", "group") is True
    assert config.is_auth_webhook_chat_allowed("oc_any", "p2p") is True


def test_auth_webhook_chat_allowed_only_for_whitelisted_groups() -> None:
    config = _minimal_config(auth_allowed_chat_ids=frozenset({"oc_allowed"}))
    assert config.is_auth_webhook_chat_allowed("oc_allowed", "group") is True
    assert config.is_auth_webhook_chat_allowed("oc_other", "group") is False
    assert config.is_auth_webhook_chat_allowed("oc_any", "p2p") is True


def test_auth_webhook_rejects_p2p_when_disabled() -> None:
    from access_assistant.channels.feishu.config import AUTH_WEBHOOK_P2P_DENIED_MESSAGE

    config = _minimal_config(auth_p2p_enabled=False)
    assert config.get_auth_webhook_deny_message("oc_any", "p2p") == AUTH_WEBHOOK_P2P_DENIED_MESSAGE
    assert config.is_auth_webhook_chat_allowed("oc_any", "p2p") is False
    assert config.is_auth_webhook_chat_allowed("oc_allowed", "group") is True


def test_auth_webhook_group_deny_message() -> None:
    from access_assistant.channels.feishu.config import AUTH_WEBHOOK_GROUP_DENIED_MESSAGE

    config = _minimal_config()
    assert config.get_auth_webhook_group_deny_message("oc_any", "group") is None
    assert config.get_auth_webhook_group_deny_message("oc_any", "p2p") is None

    config = _minimal_config(auth_allowed_chat_ids=frozenset({"oc_allowed"}))
    assert config.get_auth_webhook_group_deny_message("oc_allowed", "group") is None
    assert config.get_auth_webhook_group_deny_message("oc_other", "group") == (
        AUTH_WEBHOOK_GROUP_DENIED_MESSAGE
    )
    assert config.get_auth_webhook_group_deny_message("oc_any", "p2p") is None


def test_auth_bot_config_to_feishu_config_uses_separate_credentials() -> None:
    base = _minimal_config(app_id="cli_main", verification_token="main-token")
    auth_bot = FeishuAuthBotConfig(
        enabled=True,
        app_id="cli_auth",
        app_secret="auth-secret",
        verification_token="auth-token",
        encrypt_key=None,
        bot_open_id="ou_auth_bot",
        allowed_chat_ids=frozenset({"oc_auth_group"}),
        data_dir=None,
        p2p_enabled=False,
        show_processing_message=None,
        show_progress_updates=None,
        processing_text=None,
    )
    auth_config = auth_bot.to_feishu_config(base)
    assert auth_config.app_id == "cli_auth"
    assert auth_config.verification_token == "auth-token"
    assert auth_config.bot_open_id == "ou_auth_bot"
    assert auth_config.auth_allowed_chat_ids == frozenset({"oc_auth_group"})
    assert auth_config.thread_id_prefix == "feishu-auth"
    assert auth_config.auth_p2p_enabled is False
    assert auth_config.allowed_chat_ids == frozenset()


def test_auth_bot_config_overrides_processing_and_progress() -> None:
    base = _minimal_config(
        show_processing_message=True,
        show_progress_updates=True,
        processing_text="主机器人处理中…",
    )
    auth_bot = FeishuAuthBotConfig(
        enabled=True,
        app_id="cli_auth",
        app_secret="auth-secret",
        verification_token="auth-token",
        encrypt_key=None,
        bot_open_id="ou_auth_bot",
        allowed_chat_ids=frozenset(),
        data_dir=None,
        p2p_enabled=True,
        show_processing_message=False,
        show_progress_updates=False,
        processing_text="Auth 处理中…",
    )
    auth_config = auth_bot.to_feishu_config(base)
    assert auth_config.show_processing_message is False
    assert auth_config.show_progress_updates is False
    assert auth_config.processing_text == "Auth 处理中…"


def test_auth_bot_config_inherits_processing_when_unset() -> None:
    base = _minimal_config(show_processing_message=True, show_progress_updates=True)
    auth_bot = FeishuAuthBotConfig(
        enabled=True,
        app_id="cli_auth",
        app_secret="auth-secret",
        verification_token="auth-token",
        encrypt_key=None,
        bot_open_id="ou_auth_bot",
        allowed_chat_ids=frozenset(),
        data_dir=None,
        p2p_enabled=True,
        show_processing_message=None,
        show_progress_updates=None,
        processing_text=None,
    )
    auth_config = auth_bot.to_feishu_config(base)
    assert auth_config.show_processing_message is True
    assert auth_config.show_progress_updates is True
