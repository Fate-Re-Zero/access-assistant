import argparse
import json
import sys
import urllib.parse
import urllib.request


def invoke_custom_agent(user_question: str) -> str:
    parser = argparse.ArgumentParser(description="Call /api/health (SSE) and return full content.")
    parser.add_argument("--base-url", default="http://45.79.149.4:8000", help="Server base URL")
    parser.add_argument("--message", required=True, help="User message")
    parser.add_argument("--thread-id", default="default", help="Thread id")
    parser.add_argument("--timeout", type=float, default=300, help="Request timeout (seconds)")
    sys.argv = [sys.argv[0], "--message", user_question]
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    query = urllib.parse.urlencode({"message": args.message, "thread_id": args.thread_id})
    url = f"{base}/api/health?{query}"

    req = urllib.request.Request(
        url,
        headers={
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
        },
        method="GET",
    )

    full_content = []
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            event_name = None
            data_lines: list[str] = []
            while True:
                raw = resp.readline()
                if not raw:
                    if data_lines:
                        full_data = "\n".join(data_lines)
                        if not full_data:
                            continue
                        try:
                            payload = json.loads(full_data)
                        except json.JSONDecodeError:
                            full_content.append(f"[raw] event={event_name} data={full_data}\n")
                            data_lines = []
                            event_name = None
                            continue

                        event_type = payload.get("type") or event_name or "message"
                        if event_type == "thinking":
                            out = f"[thinking] {payload.get('content', '')}"
                        elif event_type == "text":
                            out = payload.get("content", "")
                        elif event_type == "tool_call":
                            name = payload.get("name", "unknown")
                            args_data = payload.get("args", {})
                            out = f"[tool_call] {name} args={json.dumps(args_data, ensure_ascii=False)}"
                        elif event_type == "tool_result":
                            name = payload.get("name", "unknown")
                            result = payload.get("result", "")
                            out = f"[tool_result] {name}\n{result}"
                        elif event_type == "error":
                            out = f"[error] {payload.get('message', '')}"
                        elif event_type == "done":
                            out = "[done]"
                        else:
                            out = f"[{event_type}] {json.dumps(payload, ensure_ascii=False)}"

                        if payload.get("type") != "text":
                            out += "\n"
                        full_content.append(out)

                        if payload.get("type") == "done":
                            full_content.append("\n")
                            return "".join(full_content)
                    break

                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line == "":
                    if data_lines:
                        full_data = "\n".join(data_lines)
                        if not full_data:
                            data_lines = []
                            event_name = None
                            continue
                        try:
                            payload = json.loads(full_data)
                        except json.JSONDecodeError:
                            full_content.append(f"[raw] event={event_name} data={full_data}\n")
                            data_lines = []
                            event_name = None
                            continue

                        event_type = payload.get("type") or event_name or "message"
                        if event_type == "thinking":
                            out = f"[thinking] {payload.get('content', '')}"
                        elif event_type == "text":
                            out = payload.get("content", "")
                        elif event_type == "tool_call":
                            name = payload.get("name", "unknown")
                            args_data = payload.get("args", {})
                            out = f"[tool_call] {name} args={json.dumps(args_data, ensure_ascii=False)}"
                        elif event_type == "tool_result":
                            name = payload.get("name", "unknown")
                            result = payload.get("result", "")
                            out = f"[tool_result] {name}\n{result}"
                        elif event_type == "error":
                            out = f"[error] {payload.get('message', '')}"
                        elif event_type == "done":
                            out = "[done]"
                        else:
                            out = f"[{event_type}] {json.dumps(payload, ensure_ascii=False)}"

                        if payload.get("type") != "text":
                            out += "\n"
                        full_content.append(out)

                        if payload.get("type") == "done":
                            full_content.append("\n")
                            return "".join(full_content)
                    event_name = None
                    data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_name = line[len("event:"):].strip()
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[len("data:"):].lstrip())
                    continue

    except KeyboardInterrupt:
        full_content.append("\n[interrupted]\n")
    except Exception as exc:
        full_content.append(f"[failed] {exc}\n")

    return "".join(full_content)