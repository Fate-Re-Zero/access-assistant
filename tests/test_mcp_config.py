from pathlib import Path

from access_assistant.mcp_config import MCPConfig, _parse_raw_config, load_mcp_config


def test_parse_flat_server_map():
    raw = {
        "payment-mcp-server": {
            "url": "http://paymentagent.sdo.com/sse",
            "transport": "sse",
        }
    }
    config = _parse_raw_config(raw)
    assert config.servers["payment-mcp-server"]["transport"] == "sse"
    assert config.get_servers_for_agent("payment") == ["payment-mcp-server"]


def test_parse_flat_map_with_invalid_agent_binding_falls_back_to_conventional_name(caplog):
    import logging

    caplog.set_level(logging.WARNING)
    raw = {
        "payment-mcp-server": {
            "url": "http://paymentagent.sdo.com/sse",
            "transport": "sse",
        },
        "auth-mcp-server": {
            "url": "http://auth.example.com/sse",
            "transport": "sse",
        },
        "agents": {
            "auth": ["payment-data"],
        },
    }
    config = _parse_raw_config(raw)
    assert config.get_servers_for_agent("auth") == ["auth-mcp-server"]
    assert any("payment-data" in record.message for record in caplog.records)


def test_parse_structured_config_with_agent_bindings():
    raw = {
        "servers": {
            "payment-mcp-server": {
                "url": "http://paymentagent.sdo.com/sse",
                "transport": "sse",
            },
            "logs-mcp-server": {
                "url": "http://logs.example.com/sse",
                "transport": "sse",
            },
        },
        "agents": {
            "payment": ["payment-mcp-server"],
            "integration": ["logs-mcp-server"],
        },
    }
    config = _parse_raw_config(raw)
    assert config.get_servers_for_agent("payment") == ["payment-mcp-server"]
    assert config.get_servers_for_agent("integration") == ["logs-mcp-server"]
    assert config.get_servers_for_agent("planner") == []


def test_parse_cursor_mcp_servers_wrapper():
    raw = {
        "mcpServers": {
            "weather": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-weather"],
            }
        }
    }
    config = _parse_raw_config(raw)
    assert config.servers["weather"]["transport"] == "stdio"


def test_load_from_project_file(tmp_path: Path):
    config_path = tmp_path / "mcp_servers.json"
    config_path.write_text(
        '{"demo-server":{"url":"http://example.com/sse","transport":"sse"}}',
        encoding="utf-8",
    )
    config = load_mcp_config(tmp_path)
    assert isinstance(config, MCPConfig)
    assert "demo-server" in config.servers
