from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .audit import FeishuAuditLogger
from .auth_webhook_log import (
    AUTH_WEBHOOK_LOG_PREFIX,
    log_auth_mention_check,
    log_auth_webhook,
    log_auth_whitelist_check,
)
from .client import FeishuClient
from .config import FeishuAuthBotConfig, FeishuConfig
from .dedupe import EventDeduper
from .events import (
    FeishuParseOptions,
    FeishuTextMessage,
    IgnoredEvent,
    UrlVerification,
    extract_event_type,
    extract_verification_token,
    parse_event_payload,
    parse_url_verification,
)
from .handler import AgentLike, FeishuMessageHandler
from .identity import FeishuIdentityService
from .reply_feedback import (
    build_feedback_callback_response,
    extract_card_callback_message_id,
    get_reply_card_content,
    parse_reply_feedback_callback,
)
from .session import FeishuSessionStore

log = logging.getLogger(__name__)


_PAYLOAD_LOG_MAX_CHARS = 12000
CARD_CALLBACK_LOG_PREFIX = "[FEISHU-CARD-CALLBACK]"
WebhookMode = Literal["main", "auth"]


@dataclass(frozen=True)
class FeishuWebhookInstallation:
    label: str
    config: FeishuConfig
    client: FeishuClient
    handler: FeishuMessageHandler
    storage: Any | None


def _payload_for_log(payload: dict[str, Any], *, max_chars: int = _PAYLOAD_LOG_MAX_CHARS) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        text = repr(payload)
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...(truncated, total={len(text)} chars)"


def _inbound_message_summary(payload: dict[str, Any]) -> dict[str, str]:
    event = payload.get("event")
    if not isinstance(event, dict):
        return {}
    message = event.get("message")
    if not isinstance(message, dict):
        return {}
    sender = event.get("sender")
    open_id = ""
    sender_type = ""
    if isinstance(sender, dict):
        sender_type = str(sender.get("sender_type", "")).strip()
        sender_id = sender.get("sender_id")
        if isinstance(sender_id, dict):
            open_id = str(sender_id.get("open_id", "")).strip()
    header = payload.get("header")
    event_id = ""
    if isinstance(header, dict):
        event_id = str(header.get("event_id", "")).strip()
    content = message.get("content")
    content_preview = ""
    if isinstance(content, str):
        content_preview = content[:500]
    elif content is not None:
        content_preview = str(content)[:500]
    mentions = message.get("mentions")
    mention_count = len(mentions) if isinstance(mentions, list) else 0
    return {
        "event_id": event_id,
        "chat_type": str(message.get("chat_type", "")).strip(),
        "message_type": str(message.get("message_type", "")).strip(),
        "message_id": str(message.get("message_id", "")).strip(),
        "chat_id": str(message.get("chat_id", "")).strip(),
        "open_id": open_id,
        "sender_type": sender_type,
        "mention_count": str(mention_count),
        "content_preview": content_preview,
    }


def _card_callback_summary(payload: dict[str, Any]) -> dict[str, str]:
    header = payload.get("header")
    event = payload.get("event")
    event_id = ""
    event_type = ""
    if isinstance(header, dict):
        event_id = str(header.get("event_id", "")).strip()
        event_type = str(header.get("event_type", "")).strip()
    if not event_type:
        event_type = extract_event_type(payload)

    action_value: dict[str, Any] = {}
    operator_open_id = ""
    message_id = ""
    chat_id = ""
    if isinstance(event, dict):
        action = event.get("action")
        if isinstance(action, dict):
            value = action.get("value")
            if isinstance(value, dict):
                action_value = value
        operator = event.get("operator")
        if isinstance(operator, dict):
            operator_open_id = str(operator.get("open_id", "")).strip()
        context = event.get("context")
        if isinstance(context, dict):
            message_id = str(context.get("open_message_id", "")).strip()
            chat_id = str(context.get("open_chat_id", "")).strip()

    if not message_id:
        message_id = extract_card_callback_message_id(payload)

    return {
        "event_id": event_id,
        "event_type": event_type,
        "message_id": message_id,
        "chat_id": chat_id,
        "operator_open_id": operator_open_id,
        "action_value": json.dumps(action_value, ensure_ascii=False),
    }


async def _handle_feishu_card_callback_payload(
    payload: dict[str, Any],
    *,
    installation: FeishuWebhookInstallation,
    mode: WebhookMode,
) -> JSONResponse:
    """Handle Feishu card.action.trigger callbacks (feedback buttons, etc.)."""
    config = installation.config

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Callback body must be a JSON object")

    if payload.get("encrypt"):
        raise HTTPException(
            status_code=400,
            detail="Encrypted Feishu card callbacks are not supported yet; disable Encrypt Key.",
        )

    parsed = parse_url_verification(payload)
    if parsed is not None:
        token = parsed.token or extract_verification_token(payload)
        if config.verification_token and token and token != config.verification_token:
            log.warning(
                "%s URL verification token mismatch: bot=%s mode=%s",
                CARD_CALLBACK_LOG_PREFIX,
                installation.label,
                mode,
            )
            raise HTTPException(status_code=403, detail="Invalid verification token")
        log.info(
            "%s URL verification succeeded: bot=%s mode=%s challenge=%s",
            CARD_CALLBACK_LOG_PREFIX,
            installation.label,
            mode,
            parsed.challenge,
        )
        return JSONResponse({"challenge": parsed.challenge})

    token = extract_verification_token(payload)
    if config.verification_token and token and token != config.verification_token:
        log.warning(
            "%s token mismatch: bot=%s mode=%s",
            CARD_CALLBACK_LOG_PREFIX,
            installation.label,
            mode,
        )
        raise HTTPException(status_code=403, detail="Invalid verification token")

    summary = _card_callback_summary(payload)
    feedback = parse_reply_feedback_callback(payload)
    log.info(
        "%s received: bot=%s mode=%s event_id=%s event_type=%s message_id=%s chat_id=%s "
        "operator_open_id=%s feedback=%s action_value=%s payload=%s",
        CARD_CALLBACK_LOG_PREFIX,
        installation.label,
        mode,
        summary.get("event_id") or "?",
        summary.get("event_type") or "?",
        summary.get("message_id") or "?",
        summary.get("chat_id") or "?",
        summary.get("operator_open_id") or "?",
        feedback or "unknown",
        summary.get("action_value") or "{}",
        _payload_for_log(payload),
    )

    if feedback is None or not config.reply_feedback_enabled:
        log.info(
            "%s ignored: bot=%s mode=%s feedback=%s enabled=%s",
            CARD_CALLBACK_LOG_PREFIX,
            installation.label,
            mode,
            feedback or "unknown",
            config.reply_feedback_enabled,
        )
        return JSONResponse({})

    message_id = summary.get("message_id") or extract_card_callback_message_id(payload)
    original_lark_md = get_reply_card_content(message_id)
    response_body = build_feedback_callback_response(
        feedback,
        original_lark_md=original_lark_md,
    )
    log.info(
        "%s feedback handled: bot=%s mode=%s feedback=%s message_id=%s cached=%s",
        CARD_CALLBACK_LOG_PREFIX,
        installation.label,
        mode,
        feedback,
        message_id or "?",
        bool(original_lark_md),
    )
    return JSONResponse(response_body)


def _create_installation(
    config: FeishuConfig,
    agent_provider: Callable[[], AgentLike],
    *,
    label: str,
) -> FeishuWebhookInstallation:
    client = FeishuClient(config)
    deduper = EventDeduper(max_size=config.dedupe_max_size, ttl_seconds=config.dedupe_ttl_seconds)
    storage = config.resolve_storage()
    session_store = FeishuSessionStore.from_config(
        config.data_dir,
        config.persistence_enabled,
        thread_id_prefix=config.thread_id_prefix,
    )
    audit_logger = (
        FeishuAuditLogger(storage, max_content_length=config.audit_max_content_length)
        if storage is not None and config.audit_enabled
        else None
    )
    identity_service = FeishuIdentityService(config, client) if config.sso_enabled else None
    handler = FeishuMessageHandler(
        config,
        client,
        agent_provider,
        deduper=deduper,
        session_store=session_store,
        audit_logger=audit_logger,
        identity_service=identity_service,
    )
    return FeishuWebhookInstallation(
        label=label,
        config=config,
        client=client,
        handler=handler,
        storage=storage,
    )


async def _run_auth_background_handler(
    handler: FeishuMessageHandler,
    message: FeishuTextMessage,
) -> None:
    log_auth_webhook(
        "handler",
        "started",
        message_id=message.message_id,
        chat_id=message.chat_id,
        open_id=message.open_id,
        text_preview=(message.text[:120] if message.text else ""),
    )
    try:
        await handler.process_auth_text_message(message)
    except Exception as exc:
        log_auth_webhook(
            "handler",
            "failed",
            message_id=message.message_id,
            chat_id=message.chat_id,
            error=str(exc),
        )
        log.exception("%s handler background task failed message_id=%s", AUTH_WEBHOOK_LOG_PREFIX, message.message_id)
        raise
    else:
        log_auth_webhook(
            "handler",
            "finished",
            message_id=message.message_id,
            chat_id=message.chat_id,
            open_id=message.open_id,
        )


def create_feishu_router(
    config: FeishuConfig,
    agent_provider: Callable[[], AgentLike],
    *,
    auth_bot_config: FeishuAuthBotConfig | None = None,
) -> APIRouter:
    main_installation: FeishuWebhookInstallation | None = None
    if config.enabled:
        config.validate_runtime()
        main_installation = _create_installation(config, agent_provider, label="main")

    auth_installation: FeishuWebhookInstallation | None = None
    if auth_bot_config is not None and auth_bot_config.enabled:
        auth_bot_config.validate_runtime()
        auth_config = auth_bot_config.to_feishu_config(config)
        auth_installation = _create_installation(auth_config, agent_provider, label="auth")
        log.info(
            "Feishu auth bot loaded: app_id=%s bot_open_id=%s p2p_enabled=%s "
            "show_processing=%s show_progress=%s allowed_chat_ids=%s data_dir=%s",
            auth_config.app_id,
            auth_config.bot_open_id or "(auto)",
            auth_config.auth_p2p_enabled,
            auth_config.show_processing_message,
            auth_config.show_progress_updates,
            sorted(auth_config.auth_allowed_chat_ids) or "(any group)",
            auth_config.data_dir,
        )
        if auth_bot_config.enabled and not auth_config.auth_allowed_chat_ids:
            log.warning(
                "Feishu auth bot group whitelist is empty; all groups are allowed. "
                "Set FEISHU_AUTH_ALLOWED_CHAT_IDS once in .env (duplicate keys keep the first value)."
            )

    if main_installation is not None:
        log.info(
            "Feishu main bot loaded: app_id=%s require_mention=%s group_enabled=%s",
            main_installation.config.app_id,
            main_installation.config.require_group_mention,
            main_installation.config.group_enabled,
        )
        if (
            main_installation.config.reply_feedback_enabled
            and main_installation.config.reply_feedback_use_callback
        ):
            public_base = os.getenv("FEISHU_PUBLIC_BASE_URL", "").strip().rstrip("/")
            callback_path = "/feishu/card/callback"
            callback_url = f"{public_base}{callback_path}" if public_base else callback_path
            log.info(
                "Feishu main bot card feedback callback: configure card.action.trigger URL=%s "
                "(set FEISHU_PUBLIC_BASE_URL for full URL in logs)",
                callback_url,
            )

    if auth_installation is not None:
        auth_cfg = auth_installation.config
        if auth_cfg.reply_feedback_enabled and auth_cfg.reply_feedback_use_callback:
            public_base = os.getenv("FEISHU_PUBLIC_BASE_URL", "").strip().rstrip("/")
            callback_path = "/feishu/auth/card/callback"
            callback_url = f"{public_base}{callback_path}" if public_base else callback_path
            log.info(
                "Feishu auth bot card feedback callback: configure card.action.trigger URL=%s "
                "(set FEISHU_PUBLIC_BASE_URL for full URL in logs)",
                callback_url,
            )

    router = APIRouter(tags=["feishu"])

    async def _handle_feishu_webhook_payload(
        request: Request,
        background_tasks: BackgroundTasks,
        *,
        installation: FeishuWebhookInstallation,
        mode: WebhookMode,
    ) -> JSONResponse:
        config = installation.config
        client = installation.client
        handler = installation.handler

        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}") from exc

        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Webhook body must be a JSON object")

        if mode == "auth":
            client_host = request.client.host if request.client else "unknown"
            log_auth_webhook(
                "request",
                "received",
                client=client_host,
                app_id=config.app_id,
                bot_open_id=config.bot_open_id or "(auto)",
                allowed_chat_ids=sorted(config.auth_allowed_chat_ids) or "(any)",
                p2p_enabled=config.auth_p2p_enabled,
                require_mention=config.require_group_mention,
                event_type=extract_event_type(payload) or "unknown",
            )

        event_type = extract_event_type(payload)
        log.info(
            "Feishu webhook received: bot=%s mode=%s event_type=%s payload=%s",
            installation.label,
            mode,
            event_type or "unknown",
            _payload_for_log(payload),
        )
        if event_type == "im.message.receive_v1":
            raw = _inbound_message_summary(payload)
            log.info(
                "Feishu inbound message: bot=%s mode=%s event_id=%s chat_type=%s message_type=%s "
                "message_id=%s chat_id=%s open_id=%s sender_type=%s mentions=%s content=%s",
                installation.label,
                mode,
                raw.get("event_id") or "?",
                raw.get("chat_type") or "?",
                raw.get("message_type") or "?",
                raw.get("message_id") or "?",
                raw.get("chat_id") or "?",
                raw.get("open_id") or "?",
                raw.get("sender_type") or "?",
                raw.get("mention_count") or "0",
                raw.get("content_preview") or "",
            )

        if payload.get("encrypt"):
            if mode == "auth":
                log_auth_webhook("encrypt", "rejected", reason="encrypted_payload_not_supported")
            raise HTTPException(
                status_code=400,
                detail="Encrypted Feishu events are not supported yet; disable Encrypt Key in Feishu console.",
            )

        token = extract_verification_token(payload)
        if config.verification_token and token and token != config.verification_token:
            if mode == "auth":
                log_auth_webhook(
                    "token",
                    "mismatch",
                    token_present=bool(token),
                    configured=bool(config.verification_token),
                    reason="verification_token_mismatch",
                )
            log.warning("Feishu webhook token mismatch: bot=%s mode=%s", installation.label, mode)
            raise HTTPException(status_code=403, detail="Invalid verification token")
        if mode == "auth":
            log_auth_webhook(
                "token",
                "ok",
                token_present=bool(token),
                configured=bool(config.verification_token),
            )

        if event_type == "card.action.trigger":
            return await _handle_feishu_card_callback_payload(
                payload,
                installation=installation,
                mode=mode,
            )

        bot_open_id = config.bot_open_id
        if config.group_enabled and not bot_open_id:
            try:
                bot_open_id = await client.resolve_bot_open_id()
                if mode == "auth":
                    log_auth_webhook("bot_open_id", "resolved", bot_open_id=bot_open_id)
            except Exception as exc:
                if mode == "auth":
                    log_auth_webhook("bot_open_id", "failed", error=str(exc))
                log.warning(
                    "Feishu bot open_id resolve failed: bot=%s mode=%s error=%s",
                    installation.label,
                    mode,
                    exc,
                )
        elif mode == "auth" and bot_open_id:
            log_auth_webhook("bot_open_id", "configured", bot_open_id=bot_open_id)

        parsed = parse_event_payload(
            payload,
            options=FeishuParseOptions(
                group_enabled=config.group_enabled,
                bot_open_id=bot_open_id,
            ),
        )
        if isinstance(parsed, UrlVerification):
            if mode == "auth":
                log_auth_webhook("url_verification", "ok", challenge=parsed.challenge)
            log.info(
                "Feishu URL verification succeeded: bot=%s mode=%s challenge=%s",
                installation.label,
                mode,
                parsed.challenge,
            )
            return JSONResponse({"challenge": parsed.challenge})

        if parsed is None:
            if mode == "auth":
                log_auth_webhook("parse", "failed", reason="unsupported_payload_shape")
            log.warning(
                "Feishu webhook unsupported payload shape: bot=%s mode=%s payload=%s",
                installation.label,
                mode,
                _payload_for_log(payload),
            )
            raise HTTPException(status_code=400, detail="Unsupported Feishu webhook payload")

        if isinstance(parsed, IgnoredEvent):
            if mode == "auth":
                log_auth_webhook(
                    "parse",
                    "ignored",
                    reason=parsed.reason,
                    event_type=event_type or "unknown",
                )
            log.info(
                "Feishu event ignored: bot=%s mode=%s reason=%s event_type=%s payload=%s",
                installation.label,
                mode,
                parsed.reason,
                event_type or "unknown",
                _payload_for_log(payload),
            )
            return JSONResponse({"code": 0, "msg": "ignored"})

        if isinstance(parsed, FeishuTextMessage):
            log.info(
                "Feishu message parsed: bot=%s mode=%s event_id=%s chat_type=%s message_type=%s "
                "message_id=%s chat_id=%s open_id=%s has_file=%s text_len=%s file_name=%s",
                installation.label,
                mode,
                parsed.event_id,
                parsed.chat_type,
                parsed.message_type,
                parsed.message_id,
                parsed.chat_id,
                parsed.open_id,
                parsed.has_file,
                len(parsed.text),
                parsed.file_name or "",
            )
            if parsed.chat_type == "group":
                accepted = handler.should_accept_group_message(parsed, bot_open_id)
                if mode == "auth":
                    log_auth_mention_check(
                        message=parsed,
                        bot_open_id=bot_open_id,
                        accepted=accepted,
                        require_mention=config.require_group_mention,
                        group_file_without_mention=config.group_file_without_mention,
                    )
                log.info(
                    "Feishu group mention check: bot=%s mode=%s accepted=%s require_mention=%s "
                    "group_file_without_mention=%s message_type=%s has_file=%s chat_id=%s",
                    installation.label,
                    mode,
                    accepted,
                    config.require_group_mention,
                    config.group_file_without_mention,
                    parsed.message_type,
                    parsed.has_file,
                    parsed.chat_id,
                )
                if not accepted:
                    if mode == "auth":
                        log_auth_webhook(
                            "response",
                            "ignored",
                            message_id=parsed.message_id,
                            chat_id=parsed.chat_id,
                            reason="group_mention_required",
                        )
                    log.info(
                        "Feishu group message rejected by mention rule: bot=%s message_id=%s chat_id=%s",
                        installation.label,
                        parsed.message_id,
                        parsed.chat_id,
                    )
                    return JSONResponse({"code": 0, "msg": "ignored"})

            if mode == "auth":
                allowed = log_auth_whitelist_check(
                    chat_id=parsed.chat_id,
                    chat_type=parsed.chat_type,
                    allowed_chat_ids=config.auth_allowed_chat_ids,
                    p2p_enabled=config.auth_p2p_enabled,
                )
                if not allowed:
                    deny_message = config.get_auth_webhook_deny_message(
                        parsed.chat_id,
                        parsed.chat_type,
                    )
                    assert deny_message is not None
                    deny_reason = (
                        "p2p_disabled"
                        if parsed.chat_type != "group"
                        else "group_not_in_whitelist"
                    )
                    log_auth_webhook(
                        "response",
                        "denied",
                        message_id=parsed.message_id,
                        chat_id=parsed.chat_id,
                        chat_type=parsed.chat_type,
                        reply_preview=deny_message,
                        reason=deny_reason,
                    )
                    background_tasks.add_task(
                        handler.reply_auth_webhook_denied,
                        parsed,
                        deny_message,
                    )
                    return JSONResponse({"code": 0, "msg": "ignored"})

            if handler.is_duplicate(parsed):
                if mode == "auth":
                    log_auth_webhook(
                        "dedupe",
                        "duplicate",
                        event_id=parsed.event_id,
                        message_id=parsed.message_id,
                    )
                log.info(
                    "Feishu duplicate event skipped: bot=%s mode=%s event_id=%s message_id=%s",
                    installation.label,
                    mode,
                    parsed.event_id,
                    parsed.message_id,
                )
                return JSONResponse({"code": 0, "msg": "duplicate"})

            log.info(
                "Feishu message accepted for processing: bot=%s mode=%s message_id=%s chat_id=%s open_id=%s",
                installation.label,
                mode,
                parsed.message_id,
                parsed.chat_id,
                parsed.open_id,
            )
            if mode == "auth":
                log_auth_webhook(
                    "response",
                    "accepted",
                    message_id=parsed.message_id,
                    chat_id=parsed.chat_id,
                    open_id=parsed.open_id,
                    chat_type=parsed.chat_type,
                    text_preview=(parsed.text[:120] if parsed.text else ""),
                    reason="queued_for_auth_agent",
                )
                background_tasks.add_task(_run_auth_background_handler, handler, parsed)
                return JSONResponse({"code": 0, "msg": "accepted"})

            background_tasks.add_task(handler.process_text_message, parsed)
            return JSONResponse({"code": 0, "msg": "accepted"})

        log.info(
            "Feishu webhook unhandled parse result: bot=%s mode=%s type=%s",
            installation.label,
            mode,
            type(parsed).__name__,
        )
        return JSONResponse({"code": 0, "msg": "ok"})

    @router.post("/feishu/webhook")
    async def feishu_webhook(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
        if main_installation is None:
            raise HTTPException(status_code=503, detail="Feishu main bot is not enabled")
        return await _handle_feishu_webhook_payload(
            request,
            background_tasks,
            installation=main_installation,
            mode="main",
        )

    @router.post("/feishu/auth/webhook")
    async def feishu_auth_webhook(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
        """Feishu webhook for the dedicated Auth bot (separate app credentials)."""
        if auth_installation is None:
            log_auth_webhook(
                "request",
                "rejected",
                reason="auth_bot_not_configured",
                hint="set FEISHU_AUTH_APP_ID/SECRET/VERIFICATION_TOKEN",
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "Feishu auth bot is not configured; set FEISHU_AUTH_APP_ID, "
                    "FEISHU_AUTH_APP_SECRET, and FEISHU_AUTH_VERIFICATION_TOKEN"
                ),
            )
        return await _handle_feishu_webhook_payload(
            request,
            background_tasks,
            installation=auth_installation,
            mode="auth",
        )

    @router.post("/feishu/card/callback")
    async def feishu_card_callback(request: Request) -> JSONResponse:
        """Dedicated callback URL for main bot card interactions (card.action.trigger)."""
        if main_installation is None:
            raise HTTPException(status_code=503, detail="Feishu main bot is not enabled")
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Callback body must be a JSON object")
        return await _handle_feishu_card_callback_payload(
            payload,
            installation=main_installation,
            mode="main",
        )

    @router.post("/feishu/auth/card/callback")
    async def feishu_auth_card_callback(request: Request) -> JSONResponse:
        """Dedicated callback URL for Auth bot card interactions (card.action.trigger)."""
        if auth_installation is None:
            raise HTTPException(status_code=503, detail="Feishu auth bot is not configured")
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Callback body must be a JSON object")
        return await _handle_feishu_card_callback_payload(
            payload,
            installation=auth_installation,
            mode="auth",
        )

    @router.get("/feishu/health")
    def feishu_health() -> dict[str, Any]:
        main_cfg = main_installation.config if main_installation is not None else config
        auth_cfg = auth_installation.config if auth_installation is not None else None
        mode = "p2p+group" if main_cfg.group_enabled else "p2p-only"
        return {
            "enabled": config.enabled,
            "mode": mode,
            "phase": 5,
            "main_bot": {
                "enabled": main_installation is not None,
                "app_id": main_installation.config.app_id if main_installation else "",
                "webhook_path": "/feishu/webhook",
                "card_callback_path": "/feishu/card/callback",
                "bot_open_id_configured": bool(main_cfg.bot_open_id),
            },
            "auth_bot": {
                "enabled": auth_installation is not None,
                "app_id": auth_cfg.app_id if auth_cfg else "",
                "webhook_path": "/feishu/auth/webhook",
                "card_callback_path": "/feishu/auth/card/callback",
                "bot_open_id_configured": bool(auth_cfg.bot_open_id) if auth_cfg else False,
                "allowed_chat_ids": sorted(auth_cfg.auth_allowed_chat_ids) if auth_cfg else [],
                "p2p_enabled": auth_cfg.auth_p2p_enabled if auth_cfg else True,
                "show_processing_message": auth_cfg.show_processing_message if auth_cfg else False,
                "show_progress_updates": auth_cfg.show_progress_updates if auth_cfg else False,
                "processing_text": auth_cfg.processing_text if auth_cfg else "",
                "group_whitelist_enabled": bool(auth_cfg.auth_allowed_chat_ids) if auth_cfg else False,
                "data_dir": str(auth_cfg.data_dir) if auth_cfg and auth_cfg.data_dir else None,
            },
            "group_enabled": main_cfg.group_enabled,
            "require_group_mention": main_cfg.require_group_mention,
            "group_file_without_mention": main_cfg.group_file_without_mention,
            "group_msg_permission_required": (
                main_cfg.group_enabled
                and main_cfg.require_group_mention
                and main_cfg.group_file_without_mention
            ),
            "file_inbound_enabled": main_cfg.file_inbound_enabled,
            "file_bidirectional_enabled": main_cfg.file_bidirectional_enabled,
            "file_pending_ttl_seconds": main_cfg.file_pending_ttl_seconds,
            "file_max_bytes": main_cfg.file_max_bytes,
            "file_allowed_extensions": sorted(main_cfg.file_allowed_extensions),
            "file_intent_keywords": sorted(main_cfg.file_intent_keywords),
            "file_intent_llm_enabled": main_cfg.file_intent_llm_enabled,
            "file_intent_llm_timeout_seconds": main_cfg.file_intent_llm_timeout_seconds,
            "show_processing_message": main_cfg.show_processing_message,
            "show_progress_updates": main_cfg.show_progress_updates,
            "use_lark_md": main_cfg.use_lark_md,
            "use_interactive_card": main_cfg.use_interactive_card,
            "reply_feedback_enabled": main_cfg.reply_feedback_enabled,
            "reply_feedback_use_callback": main_cfg.reply_feedback_use_callback,
            "persistence_enabled": main_cfg.persistence_enabled,
            "audit_enabled": main_cfg.audit_enabled,
            "sso_enabled": main_cfg.sso_enabled,
            "data_dir": str(main_cfg.data_dir) if main_cfg.data_dir else None,
            "whitelist_chat_ids": sorted(main_cfg.allowed_chat_ids),
            "whitelist_open_ids": sorted(main_cfg.allowed_open_ids),
        }

    @router.get("/feishu/audit/recent")
    def feishu_audit_recent(limit: int = Query(20, ge=1, le=200)) -> dict[str, Any]:
        if main_installation is None or main_installation.storage is None or not config.audit_enabled:
            raise HTTPException(status_code=404, detail="Feishu audit is disabled")

        storage = main_installation.storage
        with storage._lock, storage._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, direction, event_id, message_id, chat_id, open_id,
                       thread_id, user_name, user_email, content, status, meta_json
                FROM feishu_audit
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        items = [dict(row) for row in rows]
        return {"items": items, "count": len(items)}

    return router
