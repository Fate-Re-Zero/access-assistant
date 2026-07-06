from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any, Protocol

from .audit import FeishuAuditLogger
from .auth_webhook_log import log_auth_webhook
from ...auth_direct_scope import build_auth_direct_scoped_input
from .client import FeishuClient
from .config import FeishuConfig
from .dedupe import EventDeduper
from .events import (
    FeishuTextMessage,
    is_cancel_pending_command,
    is_help_command,
    is_new_session_command,
    should_accept_group_message,
)
from .file_intent import FileIntentClassifier, should_await_file_upload
from .files import (
    build_file_agent_prompt,
    decode_text_bytes,
    format_allowed_extensions,
    is_allowed_text_file,
    truncate_for_prompt,
)
from .identity import FeishuIdentityService, FeishuUserIdentity
from .pending import FeishuPendingStore, PendingFile, PendingQuestion
from .progress import format_progress_event, is_progress_event
from .session import FeishuSessionStore

log = logging.getLogger(__name__)

HELP_REPLY = """我是 Access Assistant，可协助处理：
- 支付/订单/到账/发货（payment）
- 集成/商户授权/签名/工单（integration）
- 账号登录/账号排查（auth）
- 业务规则与 FAQ（knowledge）

直接描述你的问题即可。
发送 .txt / .md 文件后，我会先询问你想如何处理，再开始分析。
也可先发文字说明需求，再发送文件。
发送「取消」可放弃待处理的文件/问题。
发送「/new」或「新对话」可开启新的会话。"""


class AgentLike(Protocol):
    def invoke(self, message: str, thread_id: str = "default") -> dict[str, Any]:
        ...

    def get_last_response(self, result: dict[str, Any]) -> str:
        ...

    def stream_events(self, message: str, thread_id: str = "default") -> Iterator[dict[str, Any]]:
        ...


class FeishuMessageHandler:
    def __init__(
        self,
        config: FeishuConfig,
        client: FeishuClient,
        agent_provider: Callable[[], AgentLike],
        deduper: EventDeduper | None = None,
        session_store: FeishuSessionStore | None = None,
        audit_logger: FeishuAuditLogger | None = None,
        identity_service: FeishuIdentityService | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._agent_provider = agent_provider
        self._deduper = deduper or EventDeduper(
            max_size=config.dedupe_max_size,
            ttl_seconds=config.dedupe_ttl_seconds,
        )
        self._sessions = session_store or FeishuSessionStore.from_config(
            config.data_dir,
            config.persistence_enabled,
            thread_id_prefix=config.thread_id_prefix,
        )
        self._audit = audit_logger
        self._identity = identity_service or (
            FeishuIdentityService(config, client) if config.sso_enabled else None
        )
        self._locks: dict[str, asyncio.Lock] = {}
        self._pending: FeishuPendingStore | None = None
        self._file_intent_classifier: FileIntentClassifier | None = None
        if config.file_inbound_enabled and config.file_bidirectional_enabled:
            self._pending = FeishuPendingStore(
                ttl_seconds=config.file_pending_ttl_seconds,
                max_size=config.file_pending_max_size,
            )
            self._file_intent_classifier = FileIntentClassifier(
                llm_enabled=config.file_intent_llm_enabled,
                timeout_seconds=config.file_intent_llm_timeout_seconds,
            )

    def is_duplicate(self, message: FeishuTextMessage) -> bool:
        from .events import dedupe_key

        return self._deduper.is_duplicate(dedupe_key(message))

    def should_accept_group_message(self, message: FeishuTextMessage, bot_open_id: str) -> bool:
        return should_accept_group_message(
            message,
            bot_open_id=bot_open_id,
            require_group_mention=self._config.require_group_mention,
            group_file_without_mention=self._config.group_file_without_mention,
            pending_store=self._pending,
            file_intent_keywords=self._config.file_intent_keywords,
        )

    async def process_text_message(self, message: FeishuTextMessage) -> None:
        await self._process_message(message, auth_direct=False)

    async def process_auth_text_message(self, message: FeishuTextMessage) -> None:
        """Handle Feishu text/file messages via Auth sub-agent only (no planner)."""
        await self._process_message(message, auth_direct=True)

    async def reply_auth_webhook_denied(self, message: FeishuTextMessage, text: str) -> None:
        log_auth_webhook(
            "reply",
            "deny_message",
            message_id=message.message_id,
            chat_id=message.chat_id,
            reply_preview=text,
        )
        await self._safe_reply(message.message_id, text)

    async def _process_message(self, message: FeishuTextMessage, *, auth_direct: bool) -> None:
        log.info(
            "Feishu handler start: auth_direct=%s message_id=%s chat_type=%s message_type=%s "
            "chat_id=%s open_id=%s has_file=%s text=%r",
            auth_direct,
            message.message_id,
            message.chat_type,
            message.message_type,
            message.chat_id,
            message.open_id,
            message.has_file,
            message.text[:200] if message.text else "",
        )
        lock = self._locks.setdefault(self._lock_key(message), asyncio.Lock())
        try:
            async with lock:
                await self._handle_message(message, auth_direct=auth_direct)
        except Exception as exc:
            log.exception(
                "Feishu handler failed: auth_direct=%s message_id=%s chat_id=%s error=%s",
                auth_direct,
                message.message_id,
                message.chat_id,
                exc,
            )
            await self._safe_reply(message.message_id, "处理消息时发生错误，请稍后重试。")
        else:
            log.info(
                "Feishu handler done: auth_direct=%s message_id=%s chat_id=%s open_id=%s",
                auth_direct,
                message.message_id,
                message.chat_id,
                message.open_id,
            )

    def _lock_key(self, message: FeishuTextMessage) -> str:
        if message.chat_type == "group":
            return f"{message.chat_id}:{message.open_id}"
        return message.chat_id

    async def _handle_message(
        self,
        message: FeishuTextMessage,
        *,
        auth_direct: bool = False,
    ) -> None:
        identity = await self._resolve_identity(message.open_id)

        if not self._config.is_sender_allowed(message.chat_id, message.open_id):
            log.warning(
                "Feishu sender blocked by whitelist: chat_id=%s open_id=%s",
                message.chat_id,
                message.open_id,
            )
            await self._safe_reply(message.message_id, "暂无使用权限，请联系管理员开通。")
            self._audit_event(
                message,
                direction="inbound",
                status="blocked",
                content=message.text,
                identity=identity,
                meta={"reason": "whitelist"},
            )
            return

        if self._identity is not None:
            allowed, verified_identity, reason = await self._identity.verify(message.open_id)
            identity = verified_identity or identity
            if not allowed:
                log.warning(
                    "Feishu sender blocked by SSO: chat_id=%s open_id=%s reason=%s",
                    message.chat_id,
                    message.open_id,
                    reason,
                )
                await self._safe_reply(message.message_id, reason or "暂无使用权限。")
                self._audit_event(
                    message,
                    direction="inbound",
                    status="blocked",
                    content=message.text,
                    identity=identity,
                    meta={"reason": "sso", "detail": reason},
                )
                return

        if is_new_session_command(message.text):
            if self._pending is not None:
                self._pending.clear(message.chat_id, message.open_id)
            thread_id = self._sessions.reset(message.chat_id, message.open_id)
            log.info("Feishu session reset: chat_id=%s thread_id=%s", message.chat_id, thread_id)
            reply = "已开启新对话，之前的上下文不会带入后续回复。请直接描述你的问题。"
            await self._safe_reply(message.message_id, reply)
            self._audit_event(
                message,
                direction="system",
                status="ok",
                content=message.text,
                thread_id=thread_id,
                identity=identity,
                meta={"action": "new_session"},
            )
            return

        if is_help_command(message.text):
            await self._safe_reply(message.message_id, HELP_REPLY)
            self._audit_event(
                message,
                direction="inbound",
                status="ok",
                content=message.text,
                identity=identity,
                meta={"action": "help"},
            )
            return

        if not message.has_file and is_cancel_pending_command(message.text):
            await self._handle_cancel_pending(message, identity)
            return

        if message.has_file:
            await self._handle_file_message(message, identity, auth_direct=auth_direct)
            return

        await self._handle_text_message(message, identity, auth_direct=auth_direct)

    async def _handle_cancel_pending(
        self,
        message: FeishuTextMessage,
        identity: FeishuUserIdentity | None,
    ) -> None:
        if self._pending is None or not self._pending.has_pending(message.chat_id, message.open_id):
            await self._safe_reply(message.message_id, "当前没有待处理的文件或问题。")
            return

        self._pending.clear(message.chat_id, message.open_id)
        await self._safe_reply(
            message.message_id,
            "已取消待处理的文件/问题。你可以重新发送文件或描述新的需求。",
        )
        self._audit_event(
            message,
            direction="system",
            status="ok",
            content=message.text,
            identity=identity,
            meta={"action": "cancel_pending"},
        )

    async def _handle_file_message(
        self,
        message: FeishuTextMessage,
        identity: FeishuUserIdentity | None,
        *,
        auth_direct: bool = False,
    ) -> None:
        if not self._config.file_inbound_enabled:
            await self._safe_reply(
                message.message_id,
                "文件上传功能暂未开启，请直接发送文字描述你的问题。",
            )
            return

        pending_file, error = await self._prepare_pending_file(message)
        if error or pending_file is None:
            await self._safe_reply(message.message_id, error or "文件处理失败，请稍后重试。")
            self._audit_event(
                message,
                direction="inbound",
                status="rejected",
                content=message.file_name,
                identity=identity,
                meta=self._file_audit_meta(message, reason="file_rejected"),
            )
            return

        if self._pending is None:
            prompt = build_file_agent_prompt(
                file_name=pending_file.file_name,
                file_content=pending_file.file_content,
                user_text=message.text,
                truncated=pending_file.truncated,
            )
            await self._run_agent_and_reply(
                message,
                identity,
                agent_input=prompt,
                auth_direct=auth_direct,
            )
            return

        pending_question = self._pending.get_question(message.chat_id, message.open_id)
        if pending_question is not None:
            prompt = build_file_agent_prompt(
                file_name=pending_file.file_name,
                file_content=pending_file.file_content,
                user_text=pending_question.text,
                truncated=pending_file.truncated,
            )
            self._pending.clear(message.chat_id, message.open_id)
            await self._run_agent_and_reply(
                message,
                identity,
                agent_input=prompt,
                auth_direct=auth_direct,
            )
            return

        replaced = self._pending.get_file(message.chat_id, message.open_id) is not None
        self._pending.set_file(message.chat_id, message.open_id, pending_file)
        ttl_minutes = max(int(self._config.file_pending_ttl_seconds // 60), 1)
        prefix = "已更新文件为" if replaced else "已收到文件"
        reply = (
            f"{prefix} **{pending_file.file_name}**。\n"
            f"请直接回复你想让我做什么，例如：\n"
            f"- 总结全文\n"
            f"- 提取待办事项\n"
            f"- 检查格式问题\n\n"
            f"发送「取消」可放弃；{ttl_minutes} 分钟内未回复将自动失效。"
        )
        await self._safe_reply(message.message_id, reply)
        self._audit_event(
            message,
            direction="inbound",
            status="pending",
            content=message.file_name,
            identity=identity,
            meta={
                **(self._file_audit_meta(message) or {}),
                "action": "await_question",
            },
        )

    async def _handle_text_message(
        self,
        message: FeishuTextMessage,
        identity: FeishuUserIdentity | None,
        *,
        auth_direct: bool = False,
    ) -> None:
        if self._pending is not None:
            pending_file = self._pending.get_file(message.chat_id, message.open_id)
            if pending_file is not None:
                prompt = build_file_agent_prompt(
                    file_name=pending_file.file_name,
                    file_content=pending_file.file_content,
                    user_text=message.text,
                    truncated=pending_file.truncated,
                )
                self._pending.clear(message.chat_id, message.open_id)
                await self._run_agent_and_reply(
                    message,
                    identity,
                    agent_input=prompt,
                    auth_direct=auth_direct,
                )
                return

            if await should_await_file_upload(
                message.text,
                keywords=self._config.file_intent_keywords,
                classifier=self._file_intent_classifier,
            ):
                self._pending.set_question(
                    message.chat_id,
                    message.open_id,
                    PendingQuestion(text=message.text),
                )
                allowed = format_allowed_extensions(self._config.file_allowed_extensions)
                ttl_minutes = max(int(self._config.file_pending_ttl_seconds // 60), 1)
                await self._safe_reply(
                    message.message_id,
                    f"好的，我已记录你的问题。请发送 {allowed} 文件，我会结合你的描述一起处理。\n"
                    f"发送「取消」可放弃；{ttl_minutes} 分钟内未发送文件将自动失效。",
                )
                self._audit_event(
                    message,
                    direction="inbound",
                    status="pending",
                    content=message.text,
                    identity=identity,
                    meta={"action": "await_file"},
                )
                return

        await self._run_agent_and_reply(
            message,
            identity,
            agent_input=message.text,
            auth_direct=auth_direct,
        )

    async def _resolve_identity(self, open_id: str) -> FeishuUserIdentity | None:
        if self._identity is None:
            return None
        try:
            return await self._identity.resolve(open_id)
        except Exception as exc:
            log.warning("Feishu identity resolve skipped: open_id=%s error=%s", open_id, exc)
            return None

    async def _prepare_pending_file(
        self,
        message: FeishuTextMessage,
    ) -> tuple[PendingFile | None, str | None]:
        allowed = format_allowed_extensions(self._config.file_allowed_extensions)
        if not is_allowed_text_file(message.file_name, self._config.file_allowed_extensions):
            return None, f"暂不支持该文件类型，请发送 {allowed} 格式的文本文件。"

        try:
            raw_bytes = await self._client.download_message_file(message.message_id, message.file_key)
        except Exception as exc:
            log.warning(
                "Feishu file download failed: message_id=%s file_key=%s error=%s",
                message.message_id,
                message.file_key,
                exc,
            )
            return None, "文件下载失败，请稍后重试或直接粘贴文本内容。"

        if len(raw_bytes) > self._config.file_max_bytes:
            limit_kb = max(self._config.file_max_bytes // 1024, 1)
            return None, f"文件过大（上限约 {limit_kb} KB），请拆分后重新发送。"

        try:
            file_text = decode_text_bytes(raw_bytes)
        except ValueError:
            return None, "无法识别文件编码，请另存为 UTF-8 编码的 .txt 或 .md 后重试。"

        file_text, truncated = truncate_for_prompt(file_text, self._config.file_max_prompt_chars)
        if not file_text.strip():
            return None, "文件内容为空，请检查后重新发送。"

        pending_file = PendingFile(
            file_name=message.file_name or "upload.txt",
            file_content=file_text,
            truncated=truncated,
            source_message_id=message.message_id,
        )
        log.info(
            "Feishu file prepared: message_id=%s file_name=%s bytes=%s truncated=%s",
            message.message_id,
            message.file_name,
            len(raw_bytes),
            truncated,
        )
        return pending_file, None

    def _file_audit_meta(
        self,
        message: FeishuTextMessage,
        *,
        reason: str = "",
    ) -> dict[str, Any] | None:
        if not message.has_file:
            return {"reason": reason} if reason else None
        meta: dict[str, Any] = {
            "message_type": "file",
            "file_name": message.file_name,
            "file_key": message.file_key,
        }
        if reason:
            meta["reason"] = reason
        return meta

    async def _run_agent_and_reply(
        self,
        message: FeishuTextMessage,
        identity: FeishuUserIdentity | None,
        *,
        agent_input: str | None = None,
        auth_direct: bool = False,
    ) -> None:
        user_content = agent_input if agent_input is not None else message.text
        agent_prompt = (
            build_auth_direct_scoped_input(user_content) if auth_direct else user_content
        )
        thread_id = self._sessions.build_thread_id(message.chat_id, message.open_id)
        if auth_direct:
            log_auth_webhook(
                "agent",
                "prepare",
                message_id=message.message_id,
                chat_id=message.chat_id,
                open_id=message.open_id,
                thread_id=thread_id,
                user_text_preview=(user_content[:120] if user_content else ""),
                scoped_prompt_len=len(agent_prompt),
            )
        log.info(
            "Feishu %s message received: auth_direct=%s event_id=%s message_id=%s chat_id=%s open_id=%s thread_id=%s",
            message.chat_type,
            auth_direct,
            message.event_id,
            message.message_id,
            message.chat_id,
            message.open_id,
            thread_id,
        )
        audit_meta = self._file_audit_meta(message) or {}
        if auth_direct:
            audit_meta = {**audit_meta, "agent_mode": "auth"}
        self._audit_event(
            message,
            direction="inbound",
            status="accepted",
            content=user_content,
            thread_id=thread_id,
            identity=identity,
            meta=audit_meta or None,
        )

        if self._config.show_processing_message:
            await self._safe_reply(
                message.message_id,
                self._config.processing_text,
                force_plain=True,
            )

        status = "ok"
        reply = ""
        try:
            reply = await asyncio.wait_for(
                self._invoke_agent_with_optional_progress(
                    message,
                    thread_id,
                    agent_prompt,
                    auth_direct=auth_direct,
                ),
                timeout=self._config.agent_timeout_seconds,
            )
            reply = reply.strip()
            if not reply:
                reply = "抱歉，我暂时无法生成有效回复，请换个方式描述你的问题。"
            if auth_direct:
                log_auth_webhook(
                    "agent",
                    "ok",
                    message_id=message.message_id,
                    thread_id=thread_id,
                    reply_len=len(reply),
                    reply_preview=reply[:200],
                )
        except asyncio.TimeoutError:
            if auth_direct:
                log_auth_webhook(
                    "agent",
                    "timeout",
                    message_id=message.message_id,
                    thread_id=thread_id,
                    timeout_seconds=self._config.agent_timeout_seconds,
                )
            log.warning("Feishu agent timeout: auth_direct=%s thread_id=%s", auth_direct, thread_id)
            status = "timeout"
            reply = "处理超时了，请简化问题后重试，或稍后再试。"
        except Exception as exc:
            if auth_direct:
                log_auth_webhook(
                    "agent",
                    "failed",
                    message_id=message.message_id,
                    thread_id=thread_id,
                    error=str(exc),
                )
            log.exception(
                "Feishu agent failed: auth_direct=%s thread_id=%s error=%s",
                auth_direct,
                thread_id,
                exc,
            )
            status = "error"
            reply = "处理你的问题时出现异常，请稍后再试。"

        if self._config.show_processing_message:
            await self._safe_deliver_outbound(message, reply)
        else:
            await self._safe_reply(message.message_id, reply)
            if auth_direct:
                log_auth_webhook(
                    "reply",
                    "sent" if reply else "empty",
                    message_id=message.message_id,
                    chat_id=message.chat_id,
                    reply_len=len(reply),
                    reply_preview=reply[:200],
                )

        self._audit_event(
            message,
            direction="outbound",
            status=status,
            content=reply,
            thread_id=thread_id,
            identity=identity,
            meta={"agent_mode": "auth"} if auth_direct else None,
        )

    def _resolve_agent_invokers(
        self,
        agent: AgentLike,
        *,
        auth_direct: bool,
    ) -> tuple[Any, Any | None]:
        if auth_direct:
            invoke_fn = getattr(agent, "invoke_auth", None)
            stream_fn = getattr(agent, "stream_auth_events", None)
            if invoke_fn is None:
                raise RuntimeError("Direct auth agent is not supported by the configured agent provider")
            return invoke_fn, stream_fn

        return agent.invoke, getattr(agent, "stream_events", None)

    async def _invoke_agent_with_optional_progress(
        self,
        message: FeishuTextMessage,
        thread_id: str,
        agent_input: str,
        *,
        auth_direct: bool = False,
    ) -> str:
        agent = self._agent_provider()
        invoke_fn, stream_fn = self._resolve_agent_invokers(agent, auth_direct=auth_direct)
        if stream_fn is None or not self._config.show_progress_updates:
            result = await asyncio.to_thread(invoke_fn, agent_input, thread_id)
            return agent.get_last_response(result)

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        result_holder = {"final": ""}
        stop_sentinel = object()
        worker_name = f"feishu-{'auth' if auth_direct else 'agent'}-{thread_id}"

        def worker() -> None:
            try:
                for event in stream_fn(agent_input, thread_id):
                    loop.call_soon_threadsafe(queue.put_nowait, event)
                    if str(event.get("type", "")) == "done":
                        result_holder["final"] = str(event.get("response", "")).strip()
                loop.call_soon_threadsafe(queue.put_nowait, stop_sentinel)
            except Exception as exc:
                loop.call_soon_threadsafe(queue.put_nowait, exc)

        thread = threading.Thread(target=worker, name=worker_name, daemon=True)
        thread.start()

        last_progress_at = 0.0
        seen_tool_progress: set[str] = set()
        while True:
            item = await queue.get()
            if item is stop_sentinel:
                break
            if isinstance(item, Exception):
                raise item

            event = item
            if not is_progress_event(event):
                continue

            if str(event.get("type", "")) == "tool_call":
                tool_key = ":".join(
                    [
                        str(event.get("agent_run_id") or event.get("id") or ""),
                        str(event.get("id") or ""),
                        str(event.get("name") or ""),
                    ]
                )
                if tool_key in seen_tool_progress:
                    continue
                seen_tool_progress.add(tool_key)

            now = time.monotonic()
            if now - last_progress_at < self._config.progress_min_interval_seconds:
                continue

            progress_text = format_progress_event(event)
            if not progress_text:
                continue

            await self._safe_send_progress(message, progress_text)
            last_progress_at = now

        thread.join(timeout=1.0)
        if result_holder["final"]:
            return result_holder["final"]

        result = await asyncio.to_thread(invoke_fn, agent_input, thread_id)
        return agent.get_last_response(result)

    def _audit_event(
        self,
        message: FeishuTextMessage,
        *,
        direction: str,
        status: str,
        content: str = "",
        thread_id: str | None = None,
        identity: FeishuUserIdentity | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        if self._audit is None or not self._config.audit_enabled:
            return
        try:
            self._audit.log(
                direction=direction,
                chat_id=message.chat_id,
                open_id=message.open_id,
                status=status,
                content=content,
                event_id=message.event_id,
                message_id=message.message_id,
                thread_id=thread_id,
                user_name=identity.name if identity else None,
                user_email=(identity.enterprise_email or identity.email) if identity else None,
                meta=meta,
            )
        except Exception as exc:
            log.warning("Feishu audit log failed: event_id=%s error=%s", message.event_id, exc)

    async def _safe_reply(self, message_id: str, text: str, *, force_plain: bool = False) -> None:
        try:
            await self._client.reply_text(message_id, text, force_plain=force_plain)
        except Exception as exc:
            log.exception("Feishu reply failed: message_id=%s error=%s", message_id, exc)

    async def _safe_deliver_outbound(self, message: FeishuTextMessage, text: str) -> None:
        """Deliver final answer: group chats prefer reply; p2p may send a new chat message."""
        try:
            if message.chat_type == "group":
                await self._client.reply_text(message.message_id, text)
                return
            await self._client.send_text_to_chat(message.chat_id, text)
        except Exception as exc:
            log.exception(
                "Feishu outbound failed: chat_type=%s chat_id=%s message_id=%s error=%s",
                message.chat_type,
                message.chat_id,
                message.message_id,
                exc,
            )

    async def _safe_send_to_chat(self, chat_id: str, text: str) -> None:
        try:
            await self._client.send_text_to_chat(chat_id, text)
        except Exception as exc:
            log.exception("Feishu send message failed: chat_id=%s error=%s", chat_id, exc)

    async def _safe_send_progress(self, message: FeishuTextMessage, text: str) -> None:
        try:
            if message.chat_type == "group":
                await self._client.reply_text(message.message_id, text, force_plain=True)
                return
            await self._client.send_progress_to_chat(message.chat_id, text)
        except Exception as exc:
            log.warning(
                "Feishu progress update failed: chat_type=%s chat_id=%s error=%s",
                message.chat_type,
                message.chat_id,
                exc,
            )
