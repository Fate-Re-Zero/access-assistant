#!/usr/bin/env python3
"""Probe MCP server endpoints configured in mcp_servers.json."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "mcp_servers.json"
TIMEOUT = 10


def probe(url: str, transport: str = "") -> str:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "text/event-stream, application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            status = response.status
            if transport == "sse":
                return f"OK HTTP {status} (SSE endpoint reachable)"
            body = response.read(128).decode("utf-8", errors="replace")
            return f"OK HTTP {status}, first bytes: {body[:80]!r}"
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code} {exc.reason}"
    except TimeoutError:
        if transport == "sse":
            return "TIMEOUT (SSE may still be reachable; server did not send first chunk in time)"
        return "ERROR TimeoutError: timed out"
    except Exception as exc:
        return f"ERROR {type(exc).__name__}: {exc}"


def main() -> int:
    if not CONFIG.is_file():
        print(f"Config not found: {CONFIG}", file=sys.stderr)
        return 1

    raw = json.loads(CONFIG.read_text(encoding="utf-8"))
    servers = {
        name: cfg
        for name, cfg in raw.items()
        if isinstance(cfg, dict) and cfg.get("url")
    }

    print(f"Config: {CONFIG}")
    failed = 0
    for name, cfg in servers.items():
        url = str(cfg["url"])
        result = probe(url, str(cfg.get("transport", "")))
        print(f"- {name}: {url}")
        print(f"  -> {result}")
        if not result.startswith("OK"):
            failed += 1

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
