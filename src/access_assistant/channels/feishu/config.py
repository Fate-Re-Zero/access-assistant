from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

AUTH_WEBHOOK_GROUP_DENIED_MESSAGE = "当前群聊无法使用该机器人的功能。"
AUTH_WEBHOOK_P2P_DENIED_MESSAGE = "当前机器人仅支持在指定群聊中使用，暂不支持私聊。"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_bool_optional(name: str) -> bool | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str) -> frozenset[str]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return frozenset()
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


def _env_extensions(name: str, default: str) -> frozenset[str]:
    raw = os.getenv(name, default)
    extensions: set[str] = set()
    for item in raw.split(","):
        value = item.strip().lower()
        if not value:
            continue
        if not value.startswith("."):
            value = f".{value}"
        extensions.add(value)
    return frozenset(extensions)


@dataclass(frozen=True)
class FeishuConfig:
    enabled: bool
    app_id: str
    app_secret: str
    verification_token: str
    encrypt_key: str | None
    api_base: str
    agent_timeout_seconds: float
    show_processing_message: bool
    processing_text: str
    allowed_chat_ids: frozenset[str]
    allowed_open_ids: frozenset[str]
    auth_allowed_chat_ids: frozenset[str]
    dedupe_ttl_seconds: float
    dedupe_max_size: int
    use_lark_md: bool
    use_interactive_card: bool
    reply_feedback_enabled: bool
    reply_feedback_use_callback: bool
    show_progress_updates: bool
    progress_min_interval_seconds: float
    text_chunk_size: int
    persistence_enabled: bool
    data_dir: Path | None
    audit_enabled: bool
    audit_max_content_length: int
    sso_enabled: bool
    sso_allowed_email_domains: frozenset[str]
    sso_cache_ttl_seconds: float
    group_enabled: bool
    bot_open_id: str
    require_group_mention: bool
    group_file_without_mention: bool
    file_inbound_enabled: bool
    file_max_bytes: int
    file_max_prompt_chars: int
    file_allowed_extensions: frozenset[str]
    file_bidirectional_enabled: bool
    file_pending_ttl_seconds: float
    file_pending_max_size: int
    file_intent_keywords: frozenset[str]
    file_intent_llm_enabled: bool
    file_intent_llm_timeout_seconds: float
    thread_id_prefix: str = "feishu"
    auth_p2p_enabled: bool = True

    @classmethod
    def from_env(cls) -> FeishuConfig:
        persistence_enabled = _env_bool("FEISHU_PERSISTENCE_ENABLED", True)
        data_dir_raw = os.getenv("FEISHU_DATA_DIR", "./data/feishu").strip()
        data_dir = Path(data_dir_raw) if data_dir_raw else None
        return cls(
            enabled=_env_bool("FEISHU_ENABLED", False),
            app_id=os.getenv("FEISHU_APP_ID", "").strip(),
            app_secret=os.getenv("FEISHU_APP_SECRET", "").strip(),
            verification_token=os.getenv("FEISHU_VERIFICATION_TOKEN", "").strip(),
            encrypt_key=(os.getenv("FEISHU_ENCRYPT_KEY") or "").strip() or None,
            api_base=os.getenv("FEISHU_API_BASE", "https://open.feishu.cn").rstrip("/"),
            agent_timeout_seconds=float(os.getenv("FEISHU_AGENT_TIMEOUT_SECONDS", "300")),
            show_processing_message=_env_bool("FEISHU_SHOW_PROCESSING", True),
            processing_text=os.getenv("FEISHU_PROCESSING_TEXT", "正在处理，请稍候…").strip()
            or "正在处理，请稍候…",
            allowed_chat_ids=_env_csv("FEISHU_ALLOWED_CHAT_IDS"),
            allowed_open_ids=_env_csv("FEISHU_ALLOWED_OPEN_IDS"),
            auth_allowed_chat_ids=frozenset(),
            dedupe_ttl_seconds=float(os.getenv("FEISHU_DEDUPE_TTL_SECONDS", "86400")),
            dedupe_max_size=int(os.getenv("FEISHU_DEDUPE_MAX_SIZE", "10000")),
            use_lark_md=_env_bool("FEISHU_USE_LARK_MD", True),
            use_interactive_card=_env_bool("FEISHU_USE_INTERACTIVE_CARD", True),
            reply_feedback_enabled=_env_bool("FEISHU_REPLY_FEEDBACK_ENABLED", True),
            reply_feedback_use_callback=_env_bool("FEISHU_REPLY_FEEDBACK_USE_CALLBACK", False),
            show_progress_updates=_env_bool("FEISHU_SHOW_PROGRESS_UPDATES", True),
            progress_min_interval_seconds=float(
                os.getenv("FEISHU_PROGRESS_MIN_INTERVAL_SECONDS", "3")
            ),
            text_chunk_size=int(os.getenv("FEISHU_TEXT_CHUNK_SIZE", "3800")),
            persistence_enabled=persistence_enabled,
            data_dir=data_dir if persistence_enabled else None,
            audit_enabled=_env_bool("FEISHU_AUDIT_ENABLED", True),
            audit_max_content_length=int(os.getenv("FEISHU_AUDIT_MAX_CONTENT_LENGTH", "2000")),
            sso_enabled=_env_bool("FEISHU_SSO_ENABLED", False),
            sso_allowed_email_domains=_env_csv("FEISHU_SSO_ALLOWED_EMAIL_DOMAINS"),
            sso_cache_ttl_seconds=float(os.getenv("FEISHU_SSO_CACHE_TTL_SECONDS", "3600")),
            group_enabled=_env_bool("FEISHU_GROUP_ENABLED", True),
            bot_open_id=os.getenv("FEISHU_BOT_OPEN_ID", "").strip(),
            require_group_mention=_env_bool("FEISHU_GROUP_REQUIRE_MENTION", True),
            group_file_without_mention=_env_bool("FEISHU_GROUP_FILE_WITHOUT_MENTION", True),
            file_inbound_enabled=_env_bool("FEISHU_FILE_INBOUND_ENABLED", True),
            file_max_bytes=int(os.getenv("FEISHU_FILE_MAX_BYTES", "512000")),
            file_max_prompt_chars=int(os.getenv("FEISHU_FILE_MAX_PROMPT_CHARS", "80000")),
            file_allowed_extensions=_env_extensions(
                "FEISHU_FILE_ALLOWED_EXTENSIONS",
                ".txt,.md,.markdown",
            ),
            file_bidirectional_enabled=_env_bool("FEISHU_FILE_BIDIRECTIONAL", True),
            file_pending_ttl_seconds=float(os.getenv("FEISHU_FILE_PENDING_TTL_SECONDS", "600")),
            file_pending_max_size=int(os.getenv("FEISHU_FILE_PENDING_MAX_SIZE", "5000")),
            file_intent_keywords=_env_csv("FEISHU_FILE_INTENT_KEYWORDS")
            or frozenset({"文件", "文档", "附件", "报告"}),
            file_intent_llm_enabled=_env_bool("FEISHU_FILE_INTENT_LLM_ENABLED", True),
            file_intent_llm_timeout_seconds=float(
                os.getenv("FEISHU_FILE_INTENT_LLM_TIMEOUT_SECONDS", "10")
            ),
        )

    def validate_runtime(self) -> None:
        if not self.enabled:
            return
        missing = []
        if not self.app_id:
            missing.append("FEISHU_APP_ID")
        if not self.app_secret:
            missing.append("FEISHU_APP_SECRET")
        if not self.verification_token:
            missing.append("FEISHU_VERIFICATION_TOKEN")
        if missing:
            raise ValueError(f"Feishu integration enabled but missing env: {', '.join(missing)}")
        if self.persistence_enabled and self.data_dir is None:
            raise ValueError("FEISHU_PERSISTENCE_ENABLED requires FEISHU_DATA_DIR")

    def resolve_storage(self) -> "FeishuStorage | None":
        from .storage import FeishuStorage

        if not self.persistence_enabled or self.data_dir is None:
            return None
        return FeishuStorage(self.data_dir / "feishu.sqlite")

    def is_sender_allowed(self, chat_id: str, open_id: str) -> bool:
        if self.allowed_chat_ids and chat_id not in self.allowed_chat_ids:
            return False
        if self.allowed_open_ids and open_id not in self.allowed_open_ids:
            return False
        return True

    def is_auth_webhook_chat_allowed(self, chat_id: str, chat_type: str) -> bool:
        """Restrict /feishu/auth/webhook when p2p or group rules apply."""
        return self.get_auth_webhook_deny_message(chat_id, chat_type) is None

    def get_auth_webhook_deny_message(self, chat_id: str, chat_type: str) -> str | None:
        """Return a user-facing denial message for auth-webhook chats that are not allowed."""
        if chat_type != "group":
            if not self.auth_p2p_enabled:
                return AUTH_WEBHOOK_P2P_DENIED_MESSAGE
            return None
        if not self.auth_allowed_chat_ids:
            return None
        if chat_id in self.auth_allowed_chat_ids:
            return None
        return AUTH_WEBHOOK_GROUP_DENIED_MESSAGE

    def get_auth_webhook_group_deny_message(self, chat_id: str, chat_type: str) -> str | None:
        """Return denial message for unauthorized auth-webhook group chats only."""
        if chat_type != "group":
            return None
        return self.get_auth_webhook_deny_message(chat_id, chat_type)


@dataclass(frozen=True)
class FeishuAuthBotConfig:
    """Credentials and group whitelist for the dedicated Auth Feishu bot."""

    enabled: bool
    app_id: str
    app_secret: str
    verification_token: str
    encrypt_key: str | None
    bot_open_id: str
    allowed_chat_ids: frozenset[str]
    data_dir: Path | None
    p2p_enabled: bool
    show_processing_message: bool | None
    show_progress_updates: bool | None
    processing_text: str | None

    @classmethod
    def from_env(cls) -> FeishuAuthBotConfig:
        app_id = os.getenv("FEISHU_AUTH_APP_ID", "").strip()
        explicit_enabled = os.getenv("FEISHU_AUTH_BOT_ENABLED")
        if explicit_enabled is not None:
            enabled = _env_bool("FEISHU_AUTH_BOT_ENABLED", False)
        else:
            enabled = bool(app_id)

        data_dir_raw = os.getenv("FEISHU_AUTH_DATA_DIR", "").strip()
        if data_dir_raw:
            data_dir: Path | None = Path(data_dir_raw)
        else:
            main_data_raw = os.getenv("FEISHU_DATA_DIR", "./data/feishu").strip()
            data_dir = (
                Path(main_data_raw).parent / "feishu-auth"
                if main_data_raw
                else Path("./data/feishu-auth")
            )

        return cls(
            enabled=enabled,
            app_id=app_id,
            app_secret=os.getenv("FEISHU_AUTH_APP_SECRET", "").strip(),
            verification_token=os.getenv("FEISHU_AUTH_VERIFICATION_TOKEN", "").strip(),
            encrypt_key=(os.getenv("FEISHU_AUTH_ENCRYPT_KEY") or "").strip() or None,
            bot_open_id=os.getenv("FEISHU_AUTH_BOT_OPEN_ID", "").strip(),
            allowed_chat_ids=_env_csv("FEISHU_AUTH_ALLOWED_CHAT_IDS"),
            data_dir=data_dir,
            p2p_enabled=_env_bool("FEISHU_AUTH_P2P_ENABLED", True),
            show_processing_message=_env_bool_optional("FEISHU_AUTH_SHOW_PROCESSING"),
            show_progress_updates=_env_bool_optional("FEISHU_AUTH_SHOW_PROGRESS_UPDATES"),
            processing_text=(os.getenv("FEISHU_AUTH_PROCESSING_TEXT") or "").strip() or None,
        )

    def is_configured(self) -> bool:
        return self.enabled and bool(
            self.app_id and self.app_secret and self.verification_token
        )

    def validate_runtime(self) -> None:
        if not self.enabled:
            return
        missing = []
        if not self.app_id:
            missing.append("FEISHU_AUTH_APP_ID")
        if not self.app_secret:
            missing.append("FEISHU_AUTH_APP_SECRET")
        if not self.verification_token:
            missing.append("FEISHU_AUTH_VERIFICATION_TOKEN")
        if missing:
            raise ValueError(
                f"Feishu auth bot enabled but missing env: {', '.join(missing)}"
            )

    def to_feishu_config(self, base: FeishuConfig) -> FeishuConfig:
        """Build runtime config for the Auth bot, inheriting behavior from the main config."""
        show_processing = (
            self.show_processing_message
            if self.show_processing_message is not None
            else base.show_processing_message
        )
        show_progress = (
            self.show_progress_updates
            if self.show_progress_updates is not None
            else base.show_progress_updates
        )
        processing_text = self.processing_text or base.processing_text
        return replace(
            base,
            enabled=True,
            app_id=self.app_id,
            app_secret=self.app_secret,
            verification_token=self.verification_token,
            encrypt_key=self.encrypt_key,
            bot_open_id=self.bot_open_id,
            auth_allowed_chat_ids=self.allowed_chat_ids,
            allowed_chat_ids=frozenset(),
            allowed_open_ids=frozenset(),
            sso_enabled=False,
            data_dir=self.data_dir if base.persistence_enabled else None,
            thread_id_prefix="feishu-auth",
            auth_p2p_enabled=self.p2p_enabled,
            show_processing_message=show_processing,
            show_progress_updates=show_progress,
            processing_text=processing_text,
        )
