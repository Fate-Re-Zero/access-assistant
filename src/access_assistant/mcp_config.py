"""MCP server configuration loader.

Supports multiple config formats:

1. Flat server map (recommended)::

    {
        "payment-mcp-server": {
            "url": "http://paymentagent.sdo.com/sse",
            "transport": "sse"
        }
    }

2. Structured config with per-agent bindings::

    {
        "servers": { ... },
        "agents": {
            "payment": ["payment-mcp-server"],
            "integration": ["other-mcp-server"]
        }
    }

3. Cursor-compatible ``mcpServers`` wrapper::

    {
        "mcpServers": { ... }
    }
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Agents that receive MCP tools when no explicit ``agents`` mapping exists.
DEFAULT_MCP_AGENT_KEYS = ("payment", "integration", "auth")

_SERVER_KEYS = frozenset({"transport", "url", "command", "args", "env", "headers"})


@dataclass(frozen=True)
class MCPConfig:
    """Resolved MCP configuration."""

    servers: dict[str, dict[str, Any]]
    agents: dict[str, list[str]] = field(default_factory=dict)

    def get_servers_for_agent(self, agent_key: str) -> list[str]:
        """Return MCP server names bound to an agent."""
        if not self.servers:
            return []

        if self.agents:
            names: list[str] = []
            for key in (agent_key, "*", "default"):
                for server_name in self.agents.get(key, []):
                    if server_name in self.servers:
                        if server_name not in names:
                            names.append(server_name)
                        continue
                    if server_name:
                        log.warning(
                            "MCP mapping '%s' -> '%s' ignored: server not defined (available: %s)",
                            key,
                            server_name,
                            ", ".join(sorted(self.servers)),
                        )
            if not names:
                conventional = f"{agent_key}-mcp-server"
                if conventional in self.servers:
                    log.warning(
                        "MCP agent '%s' has no valid binding; falling back to '%s'",
                        agent_key,
                        conventional,
                    )
                    return [conventional]
            return names

        if agent_key in DEFAULT_MCP_AGENT_KEYS:
            return list(self.servers.keys())
        return []

    def list_servers(self) -> list[dict[str, Any]]:
        """Expose server metadata for APIs."""
        return [
            {
                "name": name,
                "transport": config.get("transport"),
                "url": config.get("url"),
                "command": config.get("command"),
            }
            for name, config in self.servers.items()
        ]


def _is_server_config(value: Any) -> bool:
    return isinstance(value, dict) and bool(_SERVER_KEYS.intersection(value))


def _normalize_server_config(name: str, config: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(config)
    transport = str(normalized.get("transport", "")).strip().lower()
    if not transport:
        if normalized.get("command"):
            transport = "stdio"
        elif normalized.get("url"):
            transport = "http"
        else:
            raise ValueError(f"MCP server '{name}' must specify transport, url, or command")
        normalized["transport"] = transport
    return normalized


def _parse_raw_config(raw: dict[str, Any]) -> MCPConfig:
    servers: dict[str, dict[str, Any]] = {}
    agents: dict[str, list[str]] = {}

    if "servers" in raw and isinstance(raw["servers"], dict):
        for name, config in raw["servers"].items():
            if isinstance(config, dict):
                servers[name] = _normalize_server_config(name, config)
        agents_raw = raw.get("agents")
        if isinstance(agents_raw, dict):
            for agent_key, server_names in agents_raw.items():
                if isinstance(server_names, list):
                    agents[str(agent_key)] = [str(item) for item in server_names]
        return MCPConfig(servers=servers, agents=agents)

    if "mcpServers" in raw and isinstance(raw["mcpServers"], dict):
        for name, config in raw["mcpServers"].items():
            if isinstance(config, dict):
                servers[name] = _normalize_server_config(name, config)
        return MCPConfig(servers=servers, agents=agents)

    for name, config in raw.items():
        if name in {"agents", "servers", "mcpServers"}:
            continue
        if _is_server_config(config):
            servers[name] = _normalize_server_config(name, config)

    agents_raw = raw.get("agents")
    if isinstance(agents_raw, dict):
        for agent_key, server_names in agents_raw.items():
            if isinstance(server_names, list):
                agents[str(agent_key)] = [str(item) for item in server_names]

    return MCPConfig(servers=servers, agents=agents)


def _read_config_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"MCP config at {path} must be a JSON object")
    return data


def _candidate_config_paths(working_directory: Path) -> list[Path]:
    explicit_path = os.getenv("MCP_SERVERS_CONFIG_PATH", "").strip()
    if explicit_path:
        return [Path(explicit_path)]

    return [
        working_directory / "mcp_servers.json",
        working_directory / ".claude" / "mcp.json",
    ]


def load_mcp_config(working_directory: Path | None = None) -> MCPConfig:
    """Load MCP configuration from env or JSON files."""
    workdir = working_directory or Path.cwd()

    raw_env = os.getenv("MCP_SERVERS_CONFIG", "").strip()
    if raw_env:
        try:
            raw = json.loads(raw_env)
        except json.JSONDecodeError as exc:
            raise ValueError("MCP_SERVERS_CONFIG must be valid JSON") from exc
        if not isinstance(raw, dict):
            raise ValueError("MCP_SERVERS_CONFIG must be a JSON object")
        config = _parse_raw_config(raw)
        log.info("Loaded %d MCP server(s) from MCP_SERVERS_CONFIG", len(config.servers))
        return config

    for path in _candidate_config_paths(workdir):
        if not path.is_file():
            continue
        try:
            config = _parse_raw_config(_read_config_file(path))
        except Exception:
            log.exception("Failed to parse MCP config file: %s", path)
            raise
        log.info("Loaded %d MCP server(s) from %s", len(config.servers), path)
        return config

    log.info("No MCP config found; MCP tools disabled")
    return MCPConfig(servers={})
