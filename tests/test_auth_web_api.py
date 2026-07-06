from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1] / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_pkg = types.ModuleType("access_assistant")
_pkg.__path__ = [str(ROOT / "access_assistant")]
sys.modules.setdefault("access_assistant", _pkg)


def _stub_heavy_imports() -> None:
    mcp_adapters = types.ModuleType("langchain_mcp_adapters")
    mcp_client = types.ModuleType("langchain_mcp_adapters.client")
    mcp_client.MultiServerMCPClient = object  # type: ignore[attr-defined]
    mcp_adapters.client = mcp_client
    sys.modules.setdefault("langchain_mcp_adapters", mcp_adapters)
    sys.modules.setdefault("langchain_mcp_adapters.client", mcp_client)

    mcp_tools = types.ModuleType("access_assistant.mcp_tools")

    class MCPToolRegistry:  # noqa: D106
        pass

    mcp_tools.MCPToolRegistry = MCPToolRegistry
    sys.modules["access_assistant.mcp_tools"] = mcp_tools


_stub_heavy_imports()

from access_assistant.web_api import create_app  # noqa: E402


class _MockAuthAgent:
    def get_discovered_skills(self) -> list[dict[str, Any]]:
        return []

    def get_agent_registry(self) -> list[dict[str, Any]]:
        return []

    def get_mcp_servers(self) -> list[dict[str, Any]]:
        return []

    def get_system_prompt(self) -> str:
        return "mock"

    def invoke(self, message: str, thread_id: str = "default") -> dict[str, Any]:
        return {"final_response": f"supervisor:{message}"}

    def get_last_response(self, result: dict[str, Any]) -> str:
        return str(result.get("final_response", ""))

    def stream_events(self, message: str, thread_id: str = "default") -> Iterator[dict[str, Any]]:
        yield {"type": "done", "response": f"supervisor:{message}"}

    def invoke_auth(self, message: str, thread_id: str = "default") -> dict[str, Any]:
        return {
            "final_response": f"auth:{message}",
            "agent_run_id": f"{thread_id}:auth",
        }

    def stream_auth_events(self, message: str, thread_id: str = "default") -> Iterator[dict[str, Any]]:
        yield {"type": "agent_call", "agent": "auth", "id": f"{thread_id}:auth"}
        yield {"type": "done", "agent": "auth", "response": f"auth:{message}"}


@pytest.fixture
def auth_client() -> TestClient:
    app = create_app(agent_provider=lambda: _MockAuthAgent())
    return TestClient(app)


def test_auth_chat_bypasses_supervisor(auth_client: TestClient) -> None:
    response = auth_client.get(
        "/api/auth/chat",
        params={"message": "查账号 123", "thread_id": "test-auth"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["agent"] == "auth"
    assert body["message"] == "查账号 123"
    assert "查账号 123" in body["response"]
    assert "增加实名认证次数上限" in body["response"]
    assert body["agent_run_id"] == "test-auth:auth"


def test_chat_does_not_apply_auth_scope(auth_client: TestClient) -> None:
    response = auth_client.get(
        "/api/chat",
        params={"message": "查账号 123", "thread_id": "test-chat"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["response"] == "supervisor:查账号 123"


def test_auth_chat_stream_returns_sse(auth_client: TestClient) -> None:
    with auth_client.stream(
        "GET",
        "/api/auth/chat/stream",
        params={"message": "短信受限", "thread_id": "stream-auth"},
    ) as response:
        assert response.status_code == 200
        chunks = "".join(response.iter_text())
    assert "event: agent_call" in chunks
    assert "event: done" in chunks
    assert "短信受限" in chunks
    assert "增加实名认证次数上限" in chunks
