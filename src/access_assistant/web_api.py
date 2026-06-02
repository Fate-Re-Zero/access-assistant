from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from typing import Any, Protocol

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .agent import AccessAssistantAgent, check_api_credentials


# 默认允许来自本地前端开发服务器的跨域请求。
DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


class AgentLike(Protocol):
    """Minimal surface required by the Web API."""

    def get_discovered_skills(self) -> list[dict[str, Any]]:
        ...

    def get_system_prompt(self) -> str:
        ...

    def invoke(self, message: str, thread_id: str = "default") -> dict[str, Any]:
        ...

    def get_last_response(self, result: dict[str, Any]) -> str:
        ...

    def stream_events(self, message: str, thread_id: str = "default") -> Iterator[dict[str, Any]]:
        ...


# 复用一个全局单例 agent，避免每个请求都重新初始化模型和技能加载过程。
_AGENT_SINGLETON: AccessAssistantAgent | None = None


# 将内部事件编码成 SSE 文本帧，供浏览器 EventSource 持续接收。
def _to_sse_frame(event_type: str, payload: dict[str, Any]) -> str:
    """Encode one SSE frame."""
    # "error" conflicts with EventSource transport-level error events in browsers.
    # Use a dedicated SSE event name while keeping payload.type = "error".
    sse_event = "agent_error" if event_type == "error" else event_type
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {sse_event}\ndata: {data}\n\n"


# 将环境变量里的逗号分隔字符串解析成 CORS 白名单列表。
def _parse_cors_origins(raw: str | None) -> list[str]:
    """Parse comma-separated origins from env."""
    if not raw:
        return list(DEFAULT_CORS_ORIGINS)

    origins = [item.strip() for item in raw.split(",") if item.strip()]
    return origins or list(DEFAULT_CORS_ORIGINS)


# 惰性创建默认 agent，并在后续请求中复用。
def _default_agent_provider() -> AccessAssistantAgent:
    """Lazily initialize a single agent instance for API requests."""
    global _AGENT_SINGLETON
    if _AGENT_SINGLETON is None:
        _AGENT_SINGLETON = AccessAssistantAgent()
    return _AGENT_SINGLETON


# 应用工厂：集中注册中间件和所有 API 路由。
def create_app(agent_provider: Callable[[], AgentLike] | None = None) -> FastAPI:
    """Create FastAPI app with injectable agent provider (for tests)."""
    provider = agent_provider or _default_agent_provider

    app = FastAPI(
        title="LangChain Skills Agent Web API",
        version="0.1.0",
        description="SSE bridge for stream_events()",
    )

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

    return app


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
    "main",
]
