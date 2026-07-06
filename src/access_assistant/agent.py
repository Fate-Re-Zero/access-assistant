"""
Access Assistant 主体

与 claude-agent-sdk 实现的对比：
- claude-agent-sdk: setting_sources=["user", "project"] 自动处理
- LangChain 实现: 显式调用 SkillLoader，过程透明可见

流式输出支持：
- 支持 Extended Thinking 显示模型思考过程
- 事件级流式输出 (thinking / text / tool_call / tool_result)
"""

import os
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Iterator, TYPE_CHECKING

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from .skill_loader import SkillLoader
from .tools import ALL_TOOLS, SkillAgentContext
from .stream import StreamEventEmitter, ToolCallTracker, is_success, DisplayLimits
from .tool_trace import ToolTraceCallbackHandler, set_tool_trace_context, tool_call_log_enabled

if TYPE_CHECKING:
    from .mcp_tools import MCPToolRegistry

# 加载环境变量（override=True 确保 .env 文件覆盖系统环境变量）
load_dotenv(override=True)

log = logging.getLogger(__name__)
# 默认配置
DEFAULT_PROVIDER = "anthropic"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929"
DEFAULT_OPENAI_MODEL = "gpt-5-mini"
DEFAULT_MAX_TOKENS = 16000
DEFAULT_TEMPERATURE = 1.0  # Extended Thinking 要求温度为 1.0
DEFAULT_THINKING_BUDGET = 10000
DEFAULT_OPENAI_REASONING_EFFORT = "medium"
DEFAULT_ENABLE_THINKING = False


@dataclass(frozen=True)
class ModelConfig:
    """模型初始化配置"""

    provider: str
    model: str
    api_key: str | None
    base_url: str | None
    supports_extended_thinking: bool


def _normalize_provider(provider: str | None) -> str | None:
    """标准化 provider 名称"""
    if provider is None:
        return None

    normalized = provider.strip().lower()
    aliases = {
        "anthropic": "anthropic",
        "claude": "anthropic",
        "openai": "openai",
        "gpt": "openai",
        "deepseek": "openai",
    }
    return aliases.get(normalized, normalized)


def _parse_bool_env(name: str, default: bool) -> bool:
    """解析布尔环境变量"""
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _uses_direct_chat_completions_path(base_url: str) -> bool:
    """部分 OpenAI 兼容网关（如 MaxKB）在 base_url 下直接暴露 chat/completions，无 /v1 前缀。"""
    from urllib.parse import urlparse

    path = urlparse(base_url.rstrip("/")).path.lower()
    return "/chat/api/" in path


def _normalize_openai_base_url(base_url: str | None) -> str | None:
    """规范化 OpenAI SDK base_url，避免传入完整 endpoint 后被重复拼接。"""
    if not base_url:
        return base_url

    normalized = base_url.rstrip("/")
    for endpoint_suffix in ("/chat/completions", "/responses"):
        if normalized.endswith(endpoint_suffix):
            normalized = normalized[: -len(endpoint_suffix)]
            break

    if normalized.endswith("/v1"):
        return normalized
    if _uses_direct_chat_completions_path(normalized):
        return normalized
    return f"{normalized}/v1"


def _split_provider_prefixed_model(model: str | None) -> tuple[str | None, str | None]:
    """解析 provider:model 形式的模型字符串"""
    if not model or ":" not in model:
        return None, model

    raw_provider, raw_model = model.split(":", 1)
    provider = _normalize_provider(raw_provider)
    if provider in ("anthropic", "openai") and raw_model:
        return provider, raw_model
    return None, model


def _infer_provider_from_model_name(model: str | None) -> str | None:
    """根据模型名推断 provider"""
    if not model:
        return None

    model_name = model.strip().lower()
    anthropic_prefixes = ("claude-",)
    openai_prefixes = ("gpt-", "o1", "o3", "o4", "chatgpt-", "deepseek-")

    if model_name.startswith(anthropic_prefixes):
        return "anthropic"
    if model_name.startswith(openai_prefixes):
        return "openai"
    return None


def _is_deepseek_model_name(model: str | None) -> bool:
    """判断当前模型是否为 DeepSeek 系列模型。"""
    return bool(model and model.strip().lower().startswith("deepseek-"))


def _is_official_openai_base_url(base_url: str | None) -> bool:
    """判断 base_url 是否指向 OpenAI 官方 API。"""
    if not base_url:
        return True

    normalized = base_url.strip().lower().rstrip("/")
    for endpoint_suffix in ("/chat/completions", "/responses"):
        if normalized.endswith(endpoint_suffix):
            normalized = normalized[: -len(endpoint_suffix)]
            break
    if normalized.endswith("/v1"):
        normalized = normalized[: -len("/v1")]

    return normalized in {"https://api.openai.com", "http://api.openai.com"}


def _default_use_openai_responses_api(model_name: str | None, base_url: str | None) -> bool:
    """推断是否应使用 OpenAI Responses API。

    第三方 OpenAI 兼容代理通常只完整支持 chat/completions，且返回 dict 而非
    SDK 模型对象，会导致 langchain_openai 在流式解析时报
    `'dict' object has no attribute 'error'` 等错误。
    """
    if _is_deepseek_model_name(model_name):
        return False
    if base_url and not _is_official_openai_base_url(base_url):
        return False
    return True


def _resolve_requested_provider(model: str | None = None, model_provider: str | None = None) -> str:
    """解析当前请求应使用的 provider"""
    explicit_provider = _normalize_provider(model_provider or os.getenv("MODEL_PROVIDER"))
    prefixed_provider, stripped_model = _split_provider_prefixed_model(model)
    generic_env_provider, generic_env_model = _split_provider_prefixed_model(os.getenv("MODEL_NAME"))

    env_provider_hint = (
        generic_env_provider
        or _infer_provider_from_model_name(generic_env_model)
        or ("openai" if os.getenv("OPENAI_MODEL") else None)
        or ("openai" if os.getenv("DEEPSEEK_MODEL") else None)
        or ("anthropic" if os.getenv("ANTHROPIC_MODEL") or os.getenv("CLAUDE_MODEL") else None)
    )

    provider = (
        explicit_provider
        or prefixed_provider
        or _infer_provider_from_model_name(stripped_model)
        or env_provider_hint
        or DEFAULT_PROVIDER
    )

    if provider not in ("anthropic", "openai"):
        raise ValueError(f"Unsupported model provider: {provider}")

    return provider


def _resolve_model_name(provider: str, requested_model: str | None = None) -> str:
    """解析模型名称，兼容旧环境变量"""
    requested_provider, stripped_model = _split_provider_prefixed_model(requested_model)
    if requested_provider and requested_provider != provider:
        raise ValueError(
            f"Model provider mismatch: requested '{requested_provider}' but configured '{provider}'"
        )
    if stripped_model:
        return stripped_model

    generic_model = os.getenv("MODEL_NAME")
    generic_provider, stripped_generic_model = _split_provider_prefixed_model(generic_model)
    if generic_provider and generic_provider != provider:
        stripped_generic_model = None
    if stripped_generic_model:
        return stripped_generic_model

    if provider == "openai":
        return (
            os.getenv("OPENAI_MODEL")
            or os.getenv("DEEPSEEK_MODEL")
            or DEFAULT_OPENAI_MODEL
        )

    return (
        os.getenv("ANTHROPIC_MODEL")
        or os.getenv("CLAUDE_MODEL")
        or DEFAULT_ANTHROPIC_MODEL
    )


def _get_provider_credentials(provider: str) -> tuple[str | None, str | None]:
    """
    获取 provider 对应的 API 认证信息

    支持多种认证方式：
    1. 通用配置：MODEL_API_KEY / MODEL_BASE_URL
    2. Provider 专属配置
    3. OpenAI 兼容代理场景下，允许复用 ANTHROPIC_AUTH_TOKEN
       作为 OpenAI 端点的共享平台 Token

    Returns:
        (api_key, base_url) 元组
    """
    api_key = os.getenv("MODEL_API_KEY")
    base_url = os.getenv("MODEL_BASE_URL")

    if provider == "openai":
        api_key = (
            api_key
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("OPENAI_AUTH_TOKEN")
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("DEEPSEEK_AUTH_TOKEN")
            or os.getenv("ANTHROPIC_AUTH_TOKEN")
            or os.getenv("ANTHROPIC_API_KEY")
        )
        base_url = (
            base_url
            or os.getenv("OPENAI_BASE_URL")
            or os.getenv("DEEPSEEK_BASE_URL")
        )
    else:
        api_key = (
            api_key
            or os.getenv("ANTHROPIC_API_KEY")
            or os.getenv("ANTHROPIC_AUTH_TOKEN")
        )
        base_url = base_url or os.getenv("ANTHROPIC_BASE_URL")

    return api_key, base_url


def resolve_model_config(
    model: str | None = None,
    model_provider: str | None = None,
    api_key_override: str | None = None,
    base_url_override: str | None = None,
) -> ModelConfig:
    """解析当前模型配置"""
    provider = _resolve_requested_provider(model=model, model_provider=model_provider)
    model_name = _resolve_model_name(provider, model)
    api_key, base_url = _get_provider_credentials(provider)
    if api_key_override is not None:
        api_key = api_key_override
    if base_url_override is not None:
        base_url = base_url_override

    return ModelConfig(
        provider=provider,
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        supports_extended_thinking=(provider in {"anthropic", "openai"}),
    )


def check_api_credentials(model: str | None = None, model_provider: str | None = None) -> bool:
    """检查是否配置了当前 provider 的 API 认证"""
    api_key, _ = _get_provider_credentials(
        _resolve_requested_provider(model=model, model_provider=model_provider)
    )
    return api_key is not None


class AccessAssistantAgent:
    """
    使用示例：
        agent = AccessAssistantAgent()
    """

    def __init__(
        self,
        model: Optional[str] = None,
        model_provider: Optional[str] = None,
        api_key_override: Optional[str] = None,
        base_url_override: Optional[str] = None,
        extra_body_override: Optional[dict[str, Any]] = None,
        skill_paths: Optional[list[Path]] = None,
        working_directory: Optional[Path] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        enable_thinking: Optional[bool] = None,
        thinking_budget: int = DEFAULT_THINKING_BUDGET,
        system_prompt_override: Optional[str] = None,
        tools_override: Optional[list[Any]] = None,
        context_override: Optional[SkillAgentContext] = None,
        append_skill_metadata: bool = True,
        allowed_skill_names: Optional[list[str]] = None,
        mcp_registry: Optional["MCPToolRegistry"] = None,
        mcp_agent_key: Optional[str] = None,
        request_timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ):
        """
        初始化 Agent

        Args:
            model: 模型名称，默认 gpt-5.4
            model_provider: 模型提供商，支持 anthropic / openai
            api_key_override: 当前实例专用 API Key，优先级高于环境变量
            base_url_override: 当前实例专用 Base URL，优先级高于环境变量
            extra_body_override: 当前实例专用模型透传参数，如 OpenAI 兼容接口的 extra_body
            skill_paths: Skills 搜索路径
            working_directory: 工作目录
            max_tokens: 最大 tokens
            temperature: 温度参数 (启用 thinking 时强制为 1.0)
            enable_thinking: 是否启用 Extended Thinking，未传时读取 ENABLE_THINKING
            thinking_budget: thinking 的 token 预算
            system_prompt_override: 自定义 system prompt
            tools_override: 自定义工具列表
            context_override: 自定义上下文对象
            append_skill_metadata: 是否将技能元数据注入到 system prompt
            allowed_skill_names: 当前 Agent 允许访问的 skill 名称白名单
            mcp_registry: 共享 MCP 工具注册表
            mcp_agent_key: 当前 Agent 在 MCP 配置中的 key，用于选择绑定的 MCP 服务
            request_timeout: HTTP 请求超时（秒），传给底层 LLM 客户端
            max_retries: 底层 LLM SDK 自动重试次数
        """
        self.request_timeout = request_timeout
        self.max_retries = max_retries
        self.model_config = resolve_model_config(
            model=model,
            model_provider=model_provider,
            api_key_override=api_key_override,
            base_url_override=base_url_override,
        )
        self.model_provider = self.model_config.provider
        self.model_name = self.model_config.model
        self.system_prompt_override = system_prompt_override
        self.append_skill_metadata = append_skill_metadata
        self.allowed_skill_names = list(allowed_skill_names) if allowed_skill_names is not None else None
        base_tools = list(tools_override) if tools_override is not None else list(ALL_TOOLS)
        mcp_tools: list[Any] = []
        if mcp_registry is not None and mcp_agent_key:
            mcp_tools = mcp_registry.get_tools_for_agent(mcp_agent_key)
        self.tools = [*base_tools, *mcp_tools]
        self.mcp_tools = mcp_tools
        self.extra_body_override = dict(extra_body_override) if extra_body_override else None
        requested_enable_thinking = (
            enable_thinking
            if enable_thinking is not None
            else _parse_bool_env("ENABLE_THINKING", DEFAULT_ENABLE_THINKING)
        )

        # thinking 配置
        self.enable_thinking = requested_enable_thinking and self.model_config.supports_extended_thinking
        self.thinking_budget = thinking_budget

        # 配置
        self.max_tokens = max_tokens or int(os.getenv("MAX_TOKENS", str(DEFAULT_MAX_TOKENS)))
        if self.enable_thinking:
            self.temperature = 1.0  # Anthropic 要求启用 thinking 时温度为 1.0
        else:
            self.temperature = (
                temperature
                if temperature is not None
                else float(os.getenv("MODEL_TEMPERATURE", str(DEFAULT_TEMPERATURE)))
            )
        self.working_directory = working_directory or Path.cwd()
        self._tool_trace_handler: ToolTraceCallbackHandler | None = None

        # 初始化 SkillLoader
        self.skill_loader = SkillLoader(
            skill_paths,
            allowed_skill_names=self.allowed_skill_names,
        )

        self.system_prompt = self._build_system_prompt()

        # 创建上下文（供 tools 使用）
        self.context = context_override or SkillAgentContext(
            skill_loader=self.skill_loader,
            working_directory=self.working_directory,
        )

        # 创建 LangChain Agent
        self.agent = self._create_agent()

    def _build_system_prompt(self) -> str:
        """
        构建 system prompt

        将所有 Skills 的元数据注入到 system prompt，启动时一次性加载。
        """
        if self.system_prompt_override is not None:
            if self.append_skill_metadata:
                return self.skill_loader.build_system_prompt(self.system_prompt_override)
            return self.system_prompt_override

        base_prompt = """You are a helpful Access Assistant with access to specialized skills.

Your capabilities include:
- Loading and using specialized skills for specific tasks
- Executing bash commands and scripts
- Reading and writing files
- Following skill instructions to complete complex tasks
- Using MCP tools exposed by configured external servers when available

When the user asks in Chinese, answer in Chinese.
When a user request matches a skill's description, use the load_skill tool to get detailed instructions before proceeding."""

        return self.skill_loader.build_system_prompt(base_prompt)

    def _create_agent(self):
        # 构建初始化参数
        init_kwargs = {
            "model_provider": self.model_provider,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        base_url = self.model_config.base_url

        # 添加认证参数（支持第三方代理）
        if self.model_config.api_key:
            init_kwargs["api_key"] = self.model_config.api_key

        # Provider-specific thinking / reasoning 配置
        if self.model_provider == "anthropic" and self.enable_thinking:
            init_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.thinking_budget,
            }
        elif self.model_provider == "openai" and self.enable_thinking:
            use_responses_api = _parse_bool_env(
                "OPENAI_USE_RESPONSES_API",
                _default_use_openai_responses_api(self.model_name, base_url),
            )
            reasoning_effort = (
                os.getenv("OPENAI_REASONING_EFFORT")
                or os.getenv("MODEL_REASONING_EFFORT")
                or DEFAULT_OPENAI_REASONING_EFFORT
            )
            init_kwargs["use_responses_api"] = use_responses_api
            if use_responses_api:
                init_kwargs["reasoning"] = {
                    "effort": reasoning_effort,
                    "summary": os.getenv("OPENAI_REASONING_SUMMARY", "auto"),
                }
                base_url = _normalize_openai_base_url(base_url)
            else:
                init_kwargs["reasoning_effort"] = reasoning_effort
                base_url = _normalize_openai_base_url(base_url)

        if base_url:
            init_kwargs["base_url"] = base_url
        if self.extra_body_override:
            init_kwargs["extra_body"] = self.extra_body_override
        if self.request_timeout is not None:
            init_kwargs["timeout"] = self.request_timeout
        if self.max_retries is not None:
            init_kwargs["max_retries"] = self.max_retries

        # 初始化模型
        model = init_chat_model(
            self.model_name,
            **init_kwargs,
        )

        # 创建 Agent
        agent = create_agent(
            model=model,
            tools=self.tools,
            system_prompt=self.system_prompt,
            context_schema=SkillAgentContext,
        )

        return agent

    def _begin_turn(self, thread_id: str) -> None:
        self.context.active_thread_id = thread_id
        set_tool_trace_context(thread_id=thread_id, model=self.model_name)

    def _build_run_config(self, thread_id: str) -> dict[str, Any]:
        config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        self._tool_trace_handler = None
        if tool_call_log_enabled():
            self._tool_trace_handler = ToolTraceCallbackHandler(
                thread_id=thread_id,
                model=self.model_name,
            )
            config["callbacks"] = [self._tool_trace_handler]
        return config

    def _prior_message_count(self, thread_id: str) -> int:
        """No checkpoint persistence; always treat invoke as a fresh turn for tool stats."""
        return 0

    @staticmethod
    def extract_tool_names_from_messages(messages: list) -> list[str]:
        names: list[str] = []
        for msg in messages:
            if isinstance(msg, AIMessage):
                for tool_call in msg.tool_calls or []:
                    if isinstance(tool_call, dict):
                        name = str(tool_call.get("name", "")).strip()
                    else:
                        name = str(getattr(tool_call, "name", "")).strip()
                    if name:
                        names.append(name)
            elif isinstance(msg, ToolMessage):
                name = str(getattr(msg, "name", "")).strip()
                if name:
                    names.append(name)
        return names

    @staticmethod
    def extract_tool_names_from_result(result: dict, *, after_index: int = 0) -> list[str]:
        messages = result.get("messages", [])
        return AccessAssistantAgent.extract_tool_names_from_messages(messages[after_index:])

    def last_tool_calls(self) -> list[str]:
        if self._tool_trace_handler and self._tool_trace_handler.tool_calls:
            return list(self._tool_trace_handler.tool_calls)
        return []

    def invoke_with_tool_trace(self, message: str, thread_id: str = "default") -> tuple[dict, list[str]]:
        self._begin_turn(thread_id)
        config = self._build_run_config(thread_id)
        if self._tool_trace_handler:
            self._tool_trace_handler.reset()
        prior_count = self._prior_message_count(thread_id)
        result = self.agent.invoke(
            {"messages": [{"role": "user", "content": message}]},
            config=config,
            context=self.context,
        )
        traced = self.last_tool_calls()
        if traced:
            return result, traced
        return result, self.extract_tool_names_from_result(result, after_index=prior_count)

    def get_system_prompt(self) -> str:
        """
        获取当前 system prompt
        """
        return self.system_prompt

    def get_discovered_skills(self) -> list[dict]:
        """
        获取发现的 Skills 列表
        """
        skills = self.skill_loader.scan_skills()
        return [
            {
                "name": s.name,
                "description": s.description,
                "path": str(s.skill_path),
                "mcp_servers": list(s.mcp_servers),
            }
            for s in skills
        ]

    def invoke(self, message: str, thread_id: str = "default") -> dict:
        """
        同步调用 Access Assistant Agent

        Args:
            message: 用户消息
            thread_id: 会话 ID（用于多轮对话）

        Returns:
            Agent 响应
        """
        self._begin_turn(thread_id)
        config = self._build_run_config(thread_id)
        log.info("Agent invoke message=%s", message)
        result = self.agent.invoke(
            {"messages": [{"role": "user", "content": message}]},
            config=config,
            context=self.context,
        )
        result_text = repr(result)
        if len(result_text) > 5000:
            result_text = f"{result_text[:5000]}...(truncated, total={len(result_text)} chars)"
        log.info("Agent invoke result=%s", result_text)
        return result

    def stream(self, message: str, thread_id: str = "default") -> Iterator[dict]:
        """
        流式调用 Agent (state 级别)

        Args:
            message: 用户消息
            thread_id: 会话 ID

        Yields:
            流式响应块 (完整状态更新)
        """
        self._begin_turn(thread_id)
        config = self._build_run_config(thread_id)

        for chunk in self.agent.stream(
            {"messages": [{"role": "user", "content": message}]},
            config=config,
            context=self.context,
            stream_mode="values",
        ):
            yield chunk

    def stream_events(self, message: str, thread_id: str = "default") -> Iterator[dict]:
        """
        事件级流式输出，支持 thinking 和 token 级流式

        Args:
            message: 用户消息
            thread_id: 会话 ID

        Yields:
            事件字典，格式如下:
            - {"type": "thinking", "content": "..."} - 思考内容片段
            - {"type": "text", "content": "..."} - 响应文本片段
            - {"type": "tool_call", "name": "...", "args": {...}} - 工具调用
            - {"type": "tool_result", "name": "...", "content": "...", "success": bool} - 工具结果
            - {"type": "done", "response": "..."} - 完成标记，包含完整响应
        """
        self._begin_turn(thread_id)
        config = self._build_run_config(thread_id)
        emitter = StreamEventEmitter()
        tracker = ToolCallTracker()

        full_response = ""
        reasoning_tokens = 0
        thinking_seen = False
        debug = os.getenv("SKILLS_DEBUG", "").lower() in ("1", "true", "yes")

        # 使用 messages 模式获取 token 级流式
        try:
            for event in self.agent.stream(
                {"messages": [{"role": "user", "content": message}]},
                config=config,
                context=self.context,
                stream_mode="messages",
            ):
                # event 可能是 tuple(message, metadata) 或直接 message
                if isinstance(event, tuple) and len(event) >= 2:
                    chunk = event[0]
                else:
                    chunk = event

                if debug:
                    chunk_type = type(chunk).__name__
                    print(f"[DEBUG] Event: {chunk_type}")

                # 处理 AIMessageChunk / AIMessage
                if isinstance(chunk, (AIMessageChunk, AIMessage)):
                    reasoning_tokens += self._extract_reasoning_tokens(chunk)
                    # 处理 content
                    for ev in self._process_chunk_content(chunk, emitter, tracker):
                        if ev.type == "thinking":
                            thinking_seen = True
                        if ev.type == "text":
                            full_response += ev.data.get("content", "")
                        if debug:
                            print(f"[DEBUG] Yielding: {ev.type}")
                        yield ev.data

                    # 处理 tool_calls (有些情况下在 chunk.tool_calls 中)
                    if hasattr(chunk, "tool_calls") and chunk.tool_calls:
                        for ev in self._process_tool_calls(chunk.tool_calls, emitter, tracker):
                            if debug:
                                print(f"[DEBUG] Yielding from tool_calls: {ev.type}")
                            yield ev.data

                # 处理 ToolMessage (工具执行结果)
                elif hasattr(chunk, "type") and chunk.type == "tool":
                    if debug:
                        tool_name = getattr(chunk, "name", "unknown")
                        print(f"[DEBUG] Processing tool result: {tool_name}")
                    for ev in self._process_tool_result(chunk, emitter, tracker):
                        if debug:
                            print(f"[DEBUG] Yielding: {ev.type}")
                        yield ev.data

            if debug:
                print("[DEBUG] Stream completed normally")

        except Exception as e:
            if debug:
                import traceback
                print(f"[DEBUG] Stream error: {e}")
                traceback.print_exc()
            # 发送错误事件让用户知道发生了什么，避免再次抛出导致上层重复报错
            yield emitter.error(str(e)).data
            return

        if self.model_provider == "openai" and self.enable_thinking and reasoning_tokens > 0 and not thinking_seen:
            yield emitter.thinking(
                f"[OpenAI reasoning enabled: used {reasoning_tokens} reasoning tokens. "
                "This endpoint does not expose reasoning summary text in the stream.]"
            ).data

        # 发送完成事件
        yield emitter.done(full_response).data

    def _resolve_tool_call_id(self, tool_id: str, tracker: ToolCallTracker, index: int = 0) -> str:
        cleaned = (tool_id or "").strip()
        if cleaned:
            return cleaned
        return f"tool_{len(tracker.get_all()) + index + 1}"

    def _extract_openai_reasoning_text(self, chunk) -> str:
        additional = getattr(chunk, "additional_kwargs", None) or {}
        if not isinstance(additional, dict):
            return ""

        for key in ("reasoning_content", "reasoning", "reasoning_summary"):
            value = additional.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        response_metadata = getattr(chunk, "response_metadata", None) or {}
        if isinstance(response_metadata, dict):
            for key in ("reasoning_content", "reasoning", "reasoning_summary"):
                value = response_metadata.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    def _process_chunk_content(self, chunk, emitter: StreamEventEmitter, tracker: ToolCallTracker):
        """处理 chunk 的 content"""
        reasoning_text = self._extract_openai_reasoning_text(chunk)
        if reasoning_text:
            yield emitter.thinking(reasoning_text)

        content = chunk.content

        if isinstance(content, str):
            if content:
                yield emitter.text(content)
                return

        blocks = None
        if hasattr(chunk, "content_blocks"):
            try:
                blocks = chunk.content_blocks
            except Exception:
                blocks = None

        if blocks is None:
            if isinstance(content, dict):
                blocks = [content]
            elif isinstance(content, list):
                blocks = content
            else:
                return

        for raw_block in blocks:
            block = raw_block
            if not isinstance(block, dict):
                if hasattr(block, "model_dump"):
                    block = block.model_dump()
                elif hasattr(block, "dict"):
                    block = block.dict()
                else:
                    continue

            block_type = block.get("type")

            if block_type in ("thinking", "reasoning"):
                thinking_text = block.get("thinking") or block.get("reasoning") or ""
                if thinking_text:
                    yield emitter.thinking(thinking_text)

            elif block_type == "text":
                text = block.get("text") or block.get("content") or ""
                if text:
                    yield emitter.text(text)

            elif block_type in ("tool_use", "tool_call"):
                tool_id = self._resolve_tool_call_id(str(block.get("id", "")), tracker)
                name = block.get("name", "")
                args = block.get("input") if block_type == "tool_use" else block.get("args")
                args_payload = args if isinstance(args, dict) else {}

                tracker.update(tool_id, name=name, args=args_payload)
                if tracker.is_ready(tool_id):
                    tracker.mark_emitted(tool_id)
                    yield emitter.tool_call(name, args_payload, tool_id)

            elif block_type == "input_json_delta":
                # 累积 JSON 片段（args 分批到达）
                partial_json = block.get("partial_json", "")
                if partial_json:
                    tracker.append_json_delta(partial_json, block.get("index", 0))

            elif block_type == "tool_call_chunk":
                tool_id = block.get("id", "")
                name = block.get("name", "")
                if tool_id:
                    tracker.update(tool_id, name=name)
                partial_args = block.get("args", "")
                if isinstance(partial_args, str) and partial_args:
                    tracker.append_json_delta(partial_args, block.get("index", 0))

    def _handle_tool_use_block(self, block: dict, emitter: StreamEventEmitter, tracker: ToolCallTracker):
        """处理 tool_use 块 - 立即发送 tool_call 事件

        在收到 tool_use 时立即发送，让 CLI 可以显示"正在执行"状态。
        避免重复发送（同一 tool 可能通过多个路径到达）。
        """
        tool_id = self._resolve_tool_call_id(str(block.get("id", "")), tracker)
        name = block.get("name", "")
        args = block.get("input", {})
        args_payload = args if isinstance(args, dict) else {}

        tracker.update(tool_id, name=name, args=args_payload)
        if tracker.is_ready(tool_id):
            tracker.mark_emitted(tool_id)
            yield emitter.tool_call(name, args_payload, tool_id)

    def _process_tool_calls(self, tool_calls: list, emitter: StreamEventEmitter, tracker: ToolCallTracker):
        """处理 chunk.tool_calls - 立即发送 tool_call 事件

        避免重复发送（同一 tool 可能通过 tool_use block 已发送）。
        """
        for index, tc in enumerate(tool_calls):
            tool_id = self._resolve_tool_call_id(str(tc.get("id", "")), tracker, index=index)
            name = tc.get("name", "")
            args = tc.get("args", {})
            args_payload = args if isinstance(args, dict) else {}

            tracker.update(tool_id, name=name, args=args_payload)
            if tracker.is_ready(tool_id):
                tracker.mark_emitted(tool_id)
                yield emitter.tool_call(name, args_payload, tool_id)

    def _process_tool_result(self, chunk, emitter: StreamEventEmitter, tracker: ToolCallTracker):
        """处理工具结果"""
        tracker.finalize_all()

        # 发送结果
        name = getattr(chunk, "name", "unknown")
        raw_content = str(getattr(chunk, "content", ""))
        content = raw_content[:DisplayLimits.TOOL_RESULT_MAX]
        if len(raw_content) > DisplayLimits.TOOL_RESULT_MAX:
            content += "\n... (truncated)"

        # 基于内容判断是否成功（统一使用 is_success）
        success = is_success(content)

        yield emitter.tool_result(name, content, success)

    def _extract_reasoning_tokens(self, chunk) -> int:
        """从 OpenAI chunk 的 usage_metadata 中提取 reasoning token 数量"""
        usage_metadata = getattr(chunk, "usage_metadata", None) or {}
        output_details = usage_metadata.get("output_token_details") or {}
        reasoning_tokens = output_details.get("reasoning", 0)
        return reasoning_tokens if isinstance(reasoning_tokens, int) else 0

    def get_last_response(self, result: dict) -> str:
        """
        从结果中提取最后的 AI 响应文本

        Args:
            result: invoke 或 stream 的结果

        Returns:
            AI 响应文本
        """
        messages = result.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                if isinstance(msg.content, str):
                    return msg.content
                elif isinstance(msg.content, list):
                    # 处理多部分内容
                    text_parts = []
                    for part in msg.content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                        elif isinstance(part, str):
                            text_parts.append(part)
                    return "\n".join(text_parts)
        return ""


def create_access_assistant_agent(
    model: Optional[str] = None,
    model_provider: Optional[str] = None,
    api_key_override: Optional[str] = None,
    base_url_override: Optional[str] = None,
    extra_body_override: Optional[dict[str, Any]] = None,
    skill_paths: Optional[list[Path]] = None,
    allowed_skill_names: Optional[list[str]] = None,
    working_directory: Optional[Path] = None,
    enable_thinking: Optional[bool] = None,
    thinking_budget: int = DEFAULT_THINKING_BUDGET,
) -> AccessAssistantAgent:
    """
    便捷函数：创建 Access Assistant Agent

    Args:
        model: 模型名称
        model_provider: 模型提供商
        api_key_override: 当前实例专用 API Key
        base_url_override: 当前实例专用 Base URL
        extra_body_override: 当前实例专用模型透传参数
        skill_paths: Skills 搜索路径
        allowed_skill_names: 当前 Agent 允许访问的 skill 名称白名单
        working_directory: 工作目录
        enable_thinking: 是否启用 Extended Thinking，未传时读取 ENABLE_THINKING
        thinking_budget: thinking 的 token 预算

    Returns:
        配置好的 AccessAssistantAgent 实例
    """
    return AccessAssistantAgent(
        model=model,
        model_provider=model_provider,
        api_key_override=api_key_override,
        base_url_override=base_url_override,
        extra_body_override=extra_body_override,
        skill_paths=skill_paths,
        allowed_skill_names=allowed_skill_names,
        working_directory=working_directory,
        enable_thinking=enable_thinking,
        thinking_budget=thinking_budget,
    )
