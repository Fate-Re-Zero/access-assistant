import argparse
import json
import sys
import urllib.request


def invoke_custom_agent(user_question: str):
    parser = argparse.ArgumentParser(
        description="Call /api/skills and print the response JSON."
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Server base URL",
    )
    parser.add_argument(
        "--message",
        default="",
        help="User message. Note: /api/skills does not use this parameter.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30,
        help="Request timeout (seconds)",
    )
    args = parser.parse_args()

    url = f"{args.base_url.rstrip('/')}/api/skills"

    if args.message:
        sys.stderr.write(
            "[warn] /api/skills 接口不会使用用户问题参数，当前仅返回 Skills 列表。\n"
        )
        sys.stderr.flush()

    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            body = response.read().decode("utf-8")
            result = json.loads(body)
    except Exception as exc:
        result = {"error": str(exc)}

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    invoke_custom_agent("你好")
