from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable, Iterator
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from dotenv import load_dotenv

load_dotenv(override=True)

from .logging_config import configure_logging

configure_logging(force=True)

from .agent import check_api_credentials
from .auth_direct_scope import build_auth_direct_scoped_input
from .multi_agent import SupervisorSkillsAgent


# 默认允许来自本地前端开发服务器的跨域请求。
DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


class AgentLike(Protocol):
    """Minimal surface required by the Web API."""

    def get_discovered_skills(self) -> list[dict[str, Any]]:
        ...

    def get_agent_registry(self) -> list[dict[str, Any]]:
        ...

    def get_mcp_servers(self) -> list[dict[str, Any]]:
        ...

    def get_system_prompt(self) -> str:
        ...

    def invoke(self, message: str, thread_id: str = "default") -> dict[str, Any]:
        ...

    def get_last_response(self, result: dict[str, Any]) -> str:
        ...

    def stream_events(self, message: str, thread_id: str = "default") -> Iterator[dict[str, Any]]:
        ...

    def invoke_auth(self, message: str, thread_id: str = "default") -> dict[str, Any]:
        ...

    def stream_auth_events(self, message: str, thread_id: str = "default") -> Iterator[dict[str, Any]]:
        ...


# 复用一个全局单例 agent，避免每个请求都重新初始化模型和技能加载过程。
_AGENT_SINGLETON: SupervisorSkillsAgent | None = None
log = logging.getLogger(__name__)


def _should_ignore_windows_disconnect(context: dict[str, Any]) -> bool:
    """Ignore noisy SSE disconnect errors on Windows when clients close the socket."""
    if os.name != "nt":
        return False

    exception = context.get("exception")
    if not isinstance(exception, ConnectionResetError):
        return False

    if getattr(exception, "winerror", None) != 10054:
        return False

    handle = context.get("handle")
    if handle is None:
        return False

    return "_call_connection_lost" in repr(handle)


def _install_asyncio_exception_handler() -> None:
    """Install a loop exception handler that suppresses known Windows disconnect noise."""
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    def handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        if _should_ignore_windows_disconnect(context):
            log.debug("Ignored Windows SSE disconnect noise: %s", context.get("message"))
            return

        if previous_handler is not None:
            previous_handler(loop, context)
            return

        loop.default_exception_handler(context)

    loop.set_exception_handler(handler)


# 将内部事件编码成 SSE 文本帧，供浏览器 EventSource 持续接收。
def _to_sse_frame(event_type: str, payload: dict[str, Any]) -> str:
    """Encode one SSE frame."""
    # "error" conflicts with EventSource transport-level error events in browsers.
    # Use a dedicated SSE event name while keeping payload.type = "error".
    sse_event = "agent_error" if event_type == "error" else event_type
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {sse_event}\ndata: {data}\n\n"


# 将环境变量里的逗号分隔字符串解析成 CORS 白名单列表。
# 生产部署跨域时设置，例如：
# SKILLS_WEB_CORS_ORIGINS=https://your-frontend.example.com
# 前后端同域反代时可不设置；允许任意来源可设为 *
def _parse_cors_origins(raw: str | None) -> list[str]:
    """Parse comma-separated origins from env."""
    if not raw:
        return list(DEFAULT_CORS_ORIGINS)

    raw = raw.strip()
    if raw == "*":
        return ["*"]

    origins = [item.strip() for item in raw.split(",") if item.strip()]
    return origins or list(DEFAULT_CORS_ORIGINS)


def _parse_bool_env(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _ensure_agent_singleton(provider: Callable[[], SupervisorSkillsAgent]) -> SupervisorSkillsAgent:
    """Create the agent singleton if needed and log elapsed time."""
    started = time.perf_counter()
    agent = provider()
    elapsed_ms = (time.perf_counter() - started) * 1000
    log.info("Access Assistant agent singleton ready in %.1f ms", elapsed_ms)
    return agent


# 默认惰性创建；AGENT_EAGER_INIT=true 时在 FastAPI startup 预建单例。
def _default_agent_provider() -> SupervisorSkillsAgent:
    """Lazily initialize a single agent instance for API requests."""
    global _AGENT_SINGLETON
    if _AGENT_SINGLETON is None:
        _AGENT_SINGLETON = SupervisorSkillsAgent()
    return _AGENT_SINGLETON


def get_agent_singleton() -> SupervisorSkillsAgent:
    """Return the lazily initialized API agent singleton."""
    return _default_agent_provider()


def reset_agent_singleton() -> None:
    """Drop cached agent so the next request rebuilds tools/skills/MCP config."""
    global _AGENT_SINGLETON
    _AGENT_SINGLETON = None


# 应用工厂：集中注册中间件和所有 API 路由。
def create_app(agent_provider: Callable[[], AgentLike] | None = None) -> FastAPI:
    """Create FastAPI app with injectable agent provider (for tests)."""
    provider = agent_provider or _default_agent_provider
    eager_init = _parse_bool_env("AGENT_EAGER_INIT", True) and agent_provider is None
    feishu_config = _load_feishu_config()
    auth_bot_config = _load_feishu_auth_bot_config()

    app = FastAPI(
        title="Access Assistant Agent Web API",
        version="0.1.0",
        description="SSE bridge for stream_events()",
    )

    @app.on_event("startup")
    async def on_startup() -> None:
        _install_asyncio_exception_handler()
        if eager_init:
            log.info("AGENT_EAGER_INIT enabled: preloading agent singleton (MCP + planner warmup)...")
            await asyncio.to_thread(_ensure_agent_singleton, _default_agent_provider)
        if feishu_config.enabled and feishu_config.group_enabled and not feishu_config.bot_open_id:
            try:
                from .channels.feishu.client import FeishuClient

                feishu_config.validate_runtime()
                await FeishuClient(feishu_config).resolve_bot_open_id()
            except Exception as exc:
                log.warning("Feishu main bot open_id warmup failed (non-fatal): %s", exc)
        if auth_bot_config.is_configured() and not auth_bot_config.bot_open_id:
            try:
                from .channels.feishu.client import FeishuClient

                auth_bot_config.validate_runtime()
                auth_config = auth_bot_config.to_feishu_config(feishu_config)
                await FeishuClient(auth_config).resolve_bot_open_id()
            except Exception as exc:
                log.warning("Feishu auth bot open_id warmup failed (non-fatal): %s", exc)

    # 允许前端页面从不同源访问这个后端 API。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_parse_cors_origins(os.getenv("SKILLS_WEB_CORS_ORIGINS")),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 健康检查接口：用于确认服务是否存活，以及模型凭证是否已经配置。
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "api_credentials_configured": check_api_credentials(),
        }

    # 返回当前扫描到的 Skills 元数据，方便前端渲染技能列表。
    @app.get("/api/skills")
    def list_skills() -> dict[str, Any]:
        agent = provider()
        return {"skills": agent.get_discovered_skills()}

    # 暴露 system prompt，用于调试。
    @app.get("/api/prompt")
    def get_prompt() -> dict[str, str]:
        agent = provider()
        return {"prompt": agent.get_system_prompt()}

    # 返回主智能体与子智能体注册信息，供 agent-admin 同步。
    @app.get("/api/agents")
    def list_agents() -> dict[str, Any]:
        agent = provider()
        if not hasattr(agent, "get_agent_registry"):
            raise HTTPException(status_code=501, detail="Agent registry is not supported")
        registry = agent.get_agent_registry()
        return {"agents": registry}

    @app.get("/api/mcp-servers")
    def list_mcp_servers() -> dict[str, Any]:
        agent = provider()
        if not hasattr(agent, "get_mcp_servers"):
            raise HTTPException(status_code=501, detail="MCP registry is not supported")
        return {"servers": agent.get_mcp_servers()}

    @app.post("/api/reload")
    def reload_runtime() -> dict[str, Any]:
        reset_agent_singleton()
        if eager_init:
            _ensure_agent_singleton(_default_agent_provider)
            log.info("Access Assistant runtime reloaded and agent singleton rebuilt")
        else:
            log.info("Access Assistant runtime cache cleared; agent will reload on next request")
        return {"status": "ok", "reloaded": True}

    # 同步聊天接口：一次性返回 Agent 的最终文本响应，适合非流式调用场景。
    @app.get("/api/chat")
    def chat(
        message: str = Query(..., min_length=1),
        thread_id: str = Query("default", min_length=1),
    ) -> dict[str, Any]:
        try:
            agent = provider()
            result = agent.invoke(message, thread_id=thread_id)
            response = agent.get_last_response(result)
            return {
                "message": message,
                "thread_id": thread_id,
                "response": response,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    # 直连 Auth 子 Agent：跳过 planner，适合账号认证类问题调试与专用入口。
    @app.get("/api/auth/chat")
    def auth_chat(
        message: str = Query(..., min_length=1),
        thread_id: str = Query("default", min_length=1),
    ) -> dict[str, Any]:
        agent = provider()
        invoke_auth = getattr(agent, "invoke_auth", None)
        if invoke_auth is None:
            raise HTTPException(status_code=501, detail="Direct auth agent is not supported")
        try:
            agent_prompt = build_auth_direct_scoped_input(message)
            result = invoke_auth(agent_prompt, thread_id=thread_id)
            response = agent.get_last_response(result)
            return {
                "message": message,
                "thread_id": thread_id,
                "agent": "auth",
                "agent_run_id": result.get("agent_run_id"),
                "response": response,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/auth/chat/stream")
    def auth_chat_stream(
        message: str = Query(..., min_length=1),
        thread_id: str = Query("default", min_length=1),
    ) -> StreamingResponse:
        def event_stream() -> Iterator[str]:
            error_emitted = False
            try:
                agent = provider()
            except Exception as exc:  # pragma: no cover - defensive path
                payload = {"type": "error", "message": f"Failed to initialize agent: {exc}"}
                yield _to_sse_frame("error", payload)
                return

            stream_auth_events = getattr(agent, "stream_auth_events", None)
            if stream_auth_events is None:
                payload = {"type": "error", "message": "Direct auth agent is not supported"}
                yield _to_sse_frame("error", payload)
                return

            try:
                agent_prompt = build_auth_direct_scoped_input(message)
                for event in stream_auth_events(agent_prompt, thread_id=thread_id):
                    event_type = str(event.get("type", "message"))
                    if event_type == "error":
                        error_emitted = True
                    yield _to_sse_frame(event_type, event)
            except GeneratorExit:
                return
            except Exception as exc:
                if not error_emitted:
                    payload = {"type": "error", "message": str(exc)}
                    yield _to_sse_frame("error", payload)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # SSE 聊天接口：前端通过长连接持续接收 agent 产生的流式事件。
    @app.get("/api/chat/stream")
    def chat_stream(
        message: str = Query(..., min_length=1),
        thread_id: str = Query("default", min_length=1),
    ) -> StreamingResponse:
        # 这个内部生成器把 agent.stream_events() 产出的事件逐条转成 SSE 帧。
        def event_stream() -> Iterator[str]:
            error_emitted = False
            try:
                agent = provider()
            except Exception as exc:  # pragma: no cover - defensive path
                payload = {"type": "error", "message": f"Failed to initialize agent: {exc}"}
                yield _to_sse_frame("error", payload)
                return

            try:
                # 持续转发 agent 的流式事件，直到模型结束或连接中断。
                for event in agent.stream_events(message, thread_id=thread_id):
                    event_type = str(event.get("type", "message"))
                    if event_type == "error":
                        error_emitted = True
                    yield _to_sse_frame(event_type, event)
            except GeneratorExit:
                # 浏览器主动断开连接时，静默结束生成器。
                return
            except Exception as exc:
                # 如果流式过程中出现异常，尽量通过 SSE 返回一个错误事件给前端。
                if not error_emitted:
                    payload = {"type": "error", "message": str(exc)}
                    yield _to_sse_frame("error", payload)

        # 告诉客户端这是一个事件流响应，并关闭缓存以便实时显示。
        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    if feishu_config.enabled or auth_bot_config.is_configured():
        from .channels.feishu import create_feishu_router

        app.include_router(
            create_feishu_router(
                feishu_config,
                provider,
                auth_bot_config=auth_bot_config,
            )
        )
        log.info(
            "Feishu webhooks enabled: main=%s auth=%s",
            feishu_config.enabled,
            auth_bot_config.is_configured(),
        )

    from .admin_api import create_admin_router

    app.include_router(create_admin_router())
    if (os.getenv("ADMIN_INTERNAL_TOKEN") or os.getenv("ACCESS_ASSISTANT_ADMIN_TOKEN") or "").strip():
        log.info("Admin API enabled at /api/admin/*")
    else:
        log.warning("Admin API routes registered but ADMIN_INTERNAL_TOKEN is not set")

    return app


def _load_feishu_config():
    from .channels.feishu.config import FeishuConfig

    return FeishuConfig.from_env()


def _load_feishu_auth_bot_config():
    from .channels.feishu.config import FeishuAuthBotConfig

    return FeishuAuthBotConfig.from_env()


# 模块级 app，方便 uvicorn 通过 "langchain_skills.web_api:app" 直接加载。
app = create_app()


# 本地开发启动入口：从环境变量读取 host/port/reload 配置并运行 uvicorn。
def main() -> None:
    """Run development server for the Web API."""
    import uvicorn

    host = os.getenv("SKILLS_WEB_HOST", "0.0.0.0")
    port = int(os.getenv("SKILLS_WEB_PORT", "8000"))
    reload_enabled = os.getenv("SKILLS_WEB_RELOAD", "").lower() in ("1", "true", "yes")

    uvicorn.run(
        "access_assistant.web_api:app",
        host=host,
        port=port,
        reload=reload_enabled,
    )


# 显式声明该模块对外暴露的公共对象。
__all__ = [
    "app",
    "create_app",
    "get_agent_singleton",
    "main",
    "reset_agent_singleton",
]
