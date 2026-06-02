import argparse
import json
import sys
import urllib.parse
import urllib.request


def invoke_custom_agent(user_question: str):
    """
    SSE 流式接口调用，生成器逐段返回内容，单函数实现
    :param user_question: 用户提问
    :return: 生成器，迭代获取每一段流式数据
    """
    parser = argparse.ArgumentParser(description="Call /api/chat/stream (SSE) and stream events.")
    parser.add_argument("--base-url", default="http://45.79.149.4:8000", help="Server base URL")
    parser.add_argument("--message", required=True, help="User message")
    parser.add_argument("--thread-id", default="default", help="Thread id")
    parser.add_argument("--timeout", type=float, default=300, help="Request timeout (seconds)")
    sys.argv = [sys.argv[0], "--message", user_question]
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    query = urllib.parse.urlencode({"message": args.message, "thread_id": args.thread_id})
    url = f"{base}/api/chat/stream?{query}"

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
                            yield f"[raw] event={event_name} data={full_data}\n"
                            data_lines = []
                            event_name = None
                            continue

                        # 格式化内容
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

                        # 按原逻辑拼接换行
                        if payload.get("type") != "text":
                            out += "\n"
                        yield out

                        if payload.get("type") == "done":
                            yield "\n"
                            return
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
                            yield f"[raw] event={event_name} data={full_data}\n"
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
                        yield out

                        if payload.get("type") == "done":
                            yield "\n"
                            return
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
        yield "\n[interrupted]\n"
        return
    except Exception as exc:
        yield f"[failed] {exc}\n"
        return

if __name__ == "__main__":
    # 迭代流式结果
    for chunk in invoke_custom_agent("你好"):
        print(chunk, end="", flush=True)