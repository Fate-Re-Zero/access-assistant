"""Load MCP tools via langchain-mcp-adapters and expose them to agents."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from .mcp_config import MCPConfig
from .tool_trace import log_mcp_tool_call, log_mcp_tool_result

log = logging.getLogger(__name__)

_TRACE_WRAPPED_KEY = "_mcp_trace_wrapped"


def _format_mcp_load_error(exc: BaseException) -> str:
    """Unwrap TaskGroup/ExceptionGroup so logs show the real HTTP cause."""
    messages: list[str] = []

    def collect(error: BaseException) -> None:
        if isinstance(error, BaseExceptionGroup):
            for sub in error.exceptions:
                collect(sub)
            return
        text = str(error).strip()
        if text and text not in messages:
            messages.append(text)

    collect(exc)
    if messages:
        return " | ".join(messages)
    return str(exc)


def _run_async(coro):
    """Run an async coroutine from sync code."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


def wrap_mcp_tool_for_sync(tool: BaseTool) -> BaseTool:
    """Ensure MCP tools are sync-callable and emit structured MCP arg logs."""
    if not isinstance(tool, StructuredTool):
        return tool

    metadata = dict(tool.metadata or {})
    if metadata.get(_TRACE_WRAPPED_KEY):
        return tool

    source = tool
    original_func = source.func
    original_coroutine = source.coroutine
    if original_func is None and original_coroutine is None:
        return tool

    metadata[_TRACE_WRAPPED_KEY] = True

    def _execute(**kwargs: Any) -> Any:
        if original_func is not None:
            return original_func(**kwargs)
        return _run_async(original_coroutine(**kwargs))  # type: ignore[misc]

    def logged_sync(**kwargs: Any) -> Any:
        started = log_mcp_tool_call(source.name, kwargs)
        try:
            result = _execute(**kwargs)
            log_mcp_tool_result(source.name, started, success=True, result=result)
            return result
        except Exception as exc:
            log_mcp_tool_result(source.name, started, success=False, error=str(exc))
            raise

    async def logged_async(**kwargs: Any) -> Any:
        started = log_mcp_tool_call(source.name, kwargs)
        try:
            if original_coroutine is not None:
                result = await original_coroutine(**kwargs)
            else:
                result = original_func(**kwargs)  # type: ignore[misc]
            log_mcp_tool_result(source.name, started, success=True, result=result)
            return result
        except Exception as exc:
            log_mcp_tool_result(source.name, started, success=False, error=str(exc))
            raise

    return StructuredTool(
        name=source.name,
        description=source.description,
        args_schema=source.args_schema,
        func=logged_sync,
        coroutine=logged_async if original_coroutine is not None else None,
        response_format=source.response_format,
        metadata=metadata,
        handle_tool_error=source.handle_tool_error,
    )


class MCPToolRegistry:
    """Cache MCP tools per server and resolve agent-specific subsets."""

    def __init__(self, config: MCPConfig):
        self.config = config
        self._tools_by_server: dict[str, list[BaseTool]] = {}
        self._load_errors: dict[str, str] = {}
        self._loaded = False

    @property
    def load_errors(self) -> dict[str, str]:
        return dict(self._load_errors)

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.config.servers:
            return

        async def _load_all() -> None:
            client = MultiServerMCPClient(
                self.config.servers,
                tool_name_prefix=True,
                handle_tool_errors=True,
            )
            for server_name in self.config.servers:
                try:
                    tools = await client.get_tools(server_name=server_name)
                    self._tools_by_server[server_name] = [
                        wrap_mcp_tool_for_sync(tool) for tool in tools
                    ]
                    log.info(
                        "Loaded %d MCP tool(s) from server '%s'",
                        len(tools),
                        server_name,
                    )
                except Exception as exc:
                    detail = _format_mcp_load_error(exc)
                    self._load_errors[server_name] = detail
                    self._tools_by_server[server_name] = []
                    log.warning(
                        "Failed to load MCP tools from '%s': %s",
                        server_name,
                        detail,
                    )
                    log.debug(
                        "MCP load error details for '%s'",
                        server_name,
                        exc_info=exc,
                    )

        _run_async(_load_all())

    def get_tools_for_agent(self, agent_key: str) -> list[BaseTool]:
        if agent_key == "*":
            return self.get_all_tools()
        self.ensure_loaded()
        tools: list[BaseTool] = []
        seen: set[str] = set()
        for server_name in self.config.get_servers_for_agent(agent_key):
            for tool in self._tools_by_server.get(server_name, []):
                if tool.name in seen:
                    continue
                seen.add(tool.name)
                tools.append(tool)
        return tools

    def get_all_tools(self) -> list[BaseTool]:
        self.ensure_loaded()
        tools: list[BaseTool] = []
        seen: set[str] = set()
        for server_tools in self._tools_by_server.values():
            for tool in server_tools:
                if tool.name in seen:
                    continue
                seen.add(tool.name)
                tools.append(tool)
        return tools

    def describe(self) -> list[dict[str, Any]]:
        self.ensure_loaded()
        items: list[dict[str, Any]] = []
        for server in self.config.list_servers():
            name = str(server["name"])
            tools = self._tools_by_server.get(name, [])
            items.append(
                {
                    **server,
                    "tool_count": len(tools),
                    "tools": [tool.name for tool in tools],
                    "error": self._load_errors.get(name),
                }
            )
        return items
