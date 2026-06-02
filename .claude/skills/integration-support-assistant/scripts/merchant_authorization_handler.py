"""
商户授权问题处理脚本

使用方式:
    uv run .claude/skills/integration-support-assistant/scripts/merchant_authorization_handler.py \
        --params "调接口时的参数信息" \
        --return_code "错误码" \
        --return_message "错误信息" \
        --merchant_name "商户名称" \
        --ip "ip地址" \
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
class MerchantAuthorizationInput:
    """用户问题中的关键信息。"""

    params: str = ""
    return_code: str = ""
    return_message: str = ""
    merchant_name: str = ""
    ip: str = ""
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


def build_handling_message(user_input: MerchantAuthorizationInput) -> tuple[str, str]:
    """根据输入信息给出一个较稳妥的处理结论。"""

    code = user_input.return_code.strip()
    message = user_input.return_message.strip().lower()

    if not user_input.merchant_name:
        return (
            "MERCHANT_AUTH_INFO_INSUFFICIENT",
            "当前缺少商户名称，无法精准判断是否为授权问题。请补充商户名称、调用接口、请求参数、错误响应（可选，建议补充完整响应）、所属业务（可选）和请求 IP（可选），我继续帮你定位问题。",
        )
    
    if not user_input.interface:
        return (
            "MERCHANT_AUTH_INTERFACE_INSUFFICIENT",
            "当前无法确定调用的是哪个接口，无法精准定位到问题。请补充调用接口、请求参数、错误响应（可选，建议补充完整响应）、所属业务（可选）和请求 IP（可选），我继续帮你定位问题。",
        )
    
    if not user_input.params:
        return (
            "MERCHANT_AUTH_PARAMS_INSUFFICIENT",
            "当前无法确定调用的是哪个参数，无法精准定位到问题。请补充请求参数、错误响应（可选，建议补充完整响应）、所属业务（可选）和请求 IP（可选），我继续帮你定位问题。",
        )

    if code == "-10250017" or "no authority" in message:
        hand_code = "MERCHANT_AUTH_NOT_GRANTED"
        parts = [
            "从错误码和错误信息判断，这次调用是商户授权未生效或未命中授权配置。",
        ]
        if user_input.merchant_name:
            parts.append(f"当前商户名是 `{user_input.merchant_name}`，建议优先核对该商户在对应业务和接口下是否已完成授权。")
        else:
            parts.append("当前未提供商户名称，暂时无法精准核对具体授权记录。")
        if user_input.ip:
            parts.append(f"同时请核对 IP `{user_input.ip}` 是否在授权白名单中，以及是否与实际发起请求的出口 IP 一致。")
        if user_input.interface:
            parts.append(f"还需要确认接口 `{user_input.interface}` 是否属于已授权范围。")
        if user_input.biz:
            parts.append(f"如果是 `{user_input.biz}` 业务，请确认该业务线下的授权关系是否已经开通。")
        parts.append("如果你能补充授权截图、商户名、调用参数和完整响应，我可以继续帮你缩小范围。")
        return hand_code, "".join(parts)

    return (
        "MERCHANT_AUTH_NEEDS_VERIFICATION",
        "从现有信息看，问题可能与商户授权、接口权限范围或 IP 白名单配置有关，但还不能仅凭当前信息下定论。建议先核对商户授权状态、接口权限、业务归属以及实际请求出口 IP。",
    )


def build_markdown(user_input: MerchantAuthorizationInput, result: HandlingResult) -> str:
    lines = [
        "## 用户问题信息",
        "**问题类型**: 商户授权问题",
    ]

    optional_fields = [
        ("所属业务", user_input.biz),
        ("接口名称", user_input.interface),
        ("商户名称", user_input.merchant_name),
        ("调接口时的参数信息", user_input.params),
        ("ip地址", user_input.ip),
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


def handle_merchant_authorization(
    user_input: MerchantAuthorizationInput,
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
        description="商户授权问题处理工具",
        epilog="输出 JSON 和 Markdown 格式的处理结果",
    )
    parser.add_argument("--params", default="", help="调接口时的参数信息")
    parser.add_argument("--return_code", default="", help="错误码")
    parser.add_argument("--return_message", default="", help="错误信息")
    parser.add_argument("--merchant_name", default="", help="商户名称")
    parser.add_argument("--ip", default="", help="ip地址")
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
    user_input = MerchantAuthorizationInput(
        params=args.params,
        return_code=args.return_code,
        return_message=args.return_message,
        merchant_name=args.merchant_name,
        ip=args.ip,
        interface=args.interface,
        biz=args.biz,
    )
    return handle_merchant_authorization(
        user_input=user_input,
        output_dir=args.output,
        output_format=args.format,
    )


if __name__ == "__main__":
    sys.exit(main())
