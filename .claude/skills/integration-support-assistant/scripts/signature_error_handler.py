"""
签名错误问题处理脚本

使用方式:
    uv run .claude/skills/integration-support-assistant/scripts/signature_error_handler.py \
        --params "调接口时的参数信息" \
        --return_code "错误码" \
        --return_message "错误信息" \
        --interface "接口名称" \
        --biz "所属业务"
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class SignatureErrorInput:
    """用户问题中的关键信息。"""

    params: str = ""
    return_code: str = ""
    return_message: str = ""
    interface: str = ""
    biz: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class HandlingResult:
    """脚本处理结果。"""

    hand_code: str
    hand_message: str
    hand_time: str
    hand_trace_id: str

    def to_dict(self) -> dict:
        return asdict(self)


def log_info(msg: str) -> None:
    print(f"[INFO] {msg}")


def log_success(msg: str) -> None:
    print(f"[SUCCESS] {msg}")


def log_error(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)


def build_handling_message(user_input: SignatureErrorInput) -> tuple[str, str]:
    """根据入参判断可能的签名问题原因。"""

    code = user_input.return_code.strip().lower()
    message = user_input.return_message.strip().lower()
    params = user_input.params.strip()

    if code == "unauthorized-request" or "签名校验失败" in message or "signature" in message:
        hand_code = "SIGNATURE_VALIDATION_FAILED"
        parts = [
            "从错误码和错误信息判断，这是一次典型的签名校验失败问题。",
        ]
        if user_input.interface:
            parts.append(f"建议先核对接口 `{user_input.interface}` 的签名算法、待签名字段顺序、拼接规则和编码方式是否与文档一致。")
        else:
            parts.append("建议先核对当前接口的签名算法、待签名字段顺序、拼接规则和编码方式是否与文档一致。")
        if user_input.biz:
            parts.append(f"如果你接入的是 `{user_input.biz}` 业务，还要确认该业务是否有专门的签名字段或额外校验要求。")
        if params:
            parts.append("你已经提供了部分调用参数，建议重点检查参数中是否存在字段缺失、字段值被二次编码、空格换行差异，或者时间戳与随机串不符合要求。")
        else:
            parts.append("当前没有提供调用参数，暂时无法逐项比对签名串。")
        parts.append("另外也请确认使用的密钥是否正确、是否混用了测试和生产环境的密钥，以及签名前后的参数内容是否完全一致。")
        return hand_code, "".join(parts)

    if not params:
        return (
            "SIGNATURE_INFO_INSUFFICIENT",
            "当前信息不足以准确判断签名错误原因。请补充调用参数、签名原串、签名结果、错误响应以及接口名称，我再继续帮你定位。",
        )

    return (
        "SIGNATURE_NEEDS_VERIFICATION",
        "从现有信息看，问题可能出在签名串拼接、编码方式、密钥使用或环境配置不一致上，但还需要结合完整请求参数进一步确认。",
    )


def build_markdown(user_input: SignatureErrorInput, result: HandlingResult) -> str:
    lines = [
        "## 用户问题信息",
        "**问题类型**: 签名错误问题",
    ]

    optional_fields = [
        ("所属业务", user_input.biz),
        ("接口名称", user_input.interface),
        ("调接口时的参数信息", user_input.params),
        ("错误码", user_input.return_code),
        ("错误信息", user_input.return_message),
    ]

    for label, value in optional_fields:
        value = value.strip()
        if value:
            lines.append(f"**{label}**: {value}")

    lines.extend(
        [
            "",
            "---",
            "",
            "## 处理结果",
            "",
            result.hand_message,
            f"**处理结果错误码**: {result.hand_code}",
            f"**处理结果信息**: {result.hand_message}",
            f"**处理时间**: {result.hand_time}",
            f"**处理请求唯一标识**: {result.hand_trace_id}",
            "",
            "---",
            "",
        ]
    )
    return "\n".join(lines)


def build_output_name(now: datetime) -> str:
    return now.strftime("%Y%m%d%H%M%S")


def handle_signature_error(
    user_input: SignatureErrorInput,
    output_dir: str = "./output",
    output_format: str = "both",
) -> int:
    log_info("Extracting content...")

    now = datetime.now()
    hand_time = now.strftime("%Y-%m-%d %H:%M:%S")
    hand_trace_id = str(uuid.uuid4())
    hand_code, hand_message = build_handling_message(user_input)

    result = HandlingResult(
        hand_code=hand_code,
        hand_message=hand_message,
        hand_time=hand_time,
        hand_trace_id=hand_trace_id,
    )

    log_info(f"处理结果错误码: {result.hand_code}")
    log_info(f"处理结果信息: {result.hand_message}")
    log_info(f"处理时间: {result.hand_time}")
    log_info(f"处理请求唯一标识: {result.hand_trace_id}")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    output_name = build_output_name(now)

    if output_format in ("json", "both"):
        json_file = output_path / f"{output_name}.json"
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        log_success(f"Saved: {json_file}")

    if output_format in ("markdown", "both"):
        md_file = output_path / f"{output_name}.md"
        markdown_content = build_markdown(user_input, result)
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(markdown_content)
        log_success(f"Saved: {md_file}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="签名错误问题处理工具",
        epilog="输出 JSON 和 Markdown 格式的处理结果",
    )
    parser.add_argument("--params", default="", help="调接口时的参数信息")
    parser.add_argument("--return_code", default="", help="错误码")
    parser.add_argument("--return_message", default="", help="错误信息")
    parser.add_argument("--interface", default="", help="接口名称")
    parser.add_argument("--biz", default="", help="所属业务")
    parser.add_argument(
        "--output",
        "-o",
        default="./output",
        help="输出目录 (默认: ./output)",
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["json", "markdown", "both"],
        default="both",
        help="输出格式 (默认: both)",
    )

    args = parser.parse_args()
    user_input = SignatureErrorInput(
        params=args.params,
        return_code=args.return_code,
        return_message=args.return_message,
        interface=args.interface,
        biz=args.biz,
    )
    return handle_signature_error(
        user_input=user_input,
        output_dir=args.output,
        output_format=args.format,
    )


if __name__ == "__main__":
    sys.exit(main())
