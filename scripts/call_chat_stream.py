import argparse
import json
import sys
import urllib.parse
import urllib.request


def _iter_sse_events(resp):
    event_name = None
    data_lines: list[str] = []

    while True:
        raw = resp.readline()
        if not raw:
            if data_lines:
                yield event_name, "\n".join(data_lines)
            return

        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")

        if line == "":
            if data_lines:
                yield event_name, "\n".join(data_lines)
            event_name = None
            data_lines = []
            continue

        if line.startswith(":"):
            continue

        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
            continue

        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
            continue


def _format_event(event_name: str | None, payload: dict) -> str:
    event_type = payload.get("type") or event_name or "message"

    if event_type == "thinking":
        content = payload.get("content", "")
        return f"[thinking] {content}"

    if event_type == "text":
        content = payload.get("content", "")
        return content

    if event_type == "tool_call":
        name = payload.get("name", "unknown")
        args = payload.get("args", {})
        return f"[tool_call] {name} args={json.dumps(args, ensure_ascii=False)}"

    if event_type == "tool_result":
        name = payload.get("name", "unknown")
        result = payload.get("result", "")
        return f"[tool_result] {name}\n{result}"

    if event_type == "error":
        msg = payload.get("message", "")
        return f"[error] {msg}"

    if event_type == "done":
        return "[done]"

    return f"[{event_type}] {json.dumps(payload, ensure_ascii=False)}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Call /api/skills (SSE) and print streamed events.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Server base URL")
    parser.add_argument("--message", required=True, help="User message")
    parser.add_argument("--thread-id", default="default", help="Thread id")
    parser.add_argument("--timeout", type=float, default=300, help="Request timeout (seconds)")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    query = urllib.parse.urlencode({"message": args.message, "thread_id": args.thread_id})
    url = f"{base}/api/skills?{query}"

    req = urllib.request.Request(
        url,
        headers={
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            for event_name, data in _iter_sse_events(resp):
                if not data:
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    sys.stdout.write(f"[raw] event={event_name} data={data}\n")
                    sys.stdout.flush()
                    continue

                out = _format_event(event_name, payload)
                if payload.get("type") == "text":
                    sys.stdout.write(out)
                    sys.stdout.flush()
                else:
                    sys.stdout.write(out + "\n")
                    sys.stdout.flush()

                if payload.get("type") == "done":
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return 0
    except KeyboardInterrupt:
        sys.stdout.write("\n[interrupted]\n")
        return 130
    except Exception as exc:
        sys.stderr.write(f"[failed] {exc}\n")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())