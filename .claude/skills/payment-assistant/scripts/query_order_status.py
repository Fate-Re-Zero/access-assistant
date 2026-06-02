"""
查询订单是否发货脚本

使用方式:
    uv run query_order_status.py "订单号"
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

# 添加当前目录到路径
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from formatter import to_markdown

from dataclasses import dataclass
from models import OrderInfo

def log_info(msg: str) -> None:
    """输出信息日志"""
    print(f"[INFO] {msg}")


def log_success(msg: str) -> None:
    """输出成功日志"""
    print(f"[SUCCESS] {msg}")


def log_error(msg: str) -> None:
    """输出错误日志"""
    print(f"[ERROR] {msg}", file=sys.stderr)

def query_order_status(
    order_id: str,
    output_dir: str = "./output",
    output_format: str = "both",
    biz_type: Optional[str] = None,
    env: Optional[str] = None,
) -> int:
    """
    查询订单是否发货

    Args:
        order_id: 订单号
        output_dir: 输出目录
        output_format: 输出格式 (json, markdown, both)
        biz_type: 指定业务类型（可选，默认国内游戏外充值）
        env: 指定环境（可选，默认测试环境）

    Returns:
        0 表示成功，1 表示失败
    """
    print(f"query_order_status function, Start, Order ID: {order_id}")

       # 1. 查询订单状态
    log_info("Order Info...")
    try:
        log_info(f"query_order_status function, Invoke Query Order Interface, Order ID: {order_id}")
        new_order = OrderInfo(order_id=order_id)
        new_order.app_id = "79100123"
        new_order.game_name = "测试游戏"
        new_order.account_id = "1234567890"
        new_order.pay_time = "2026-05-12 12:00:00"
        new_order.result_message = "订单未发货"
        new_order.uuid = "ABC123456"
    except ValueError as e:
        log_error(f"处理失败: {e}")
        return 1
    except Exception as e:
        log_error(f"未知错误: {e}")
        return 1

    # 2. 显示订单信息
    log_info(f"{new_order}")

    # 3. 创建输出目录
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # 4. 生成输出文件
    order_id = new_order.order_id or "untitled"
    # 清理文件名中的非法字符
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in order_id)

    # JSON 输出
    if output_format in ("json", "both"):
        json_file = output_path / f"{safe_id}.json"
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(new_order.to_dict(), f, ensure_ascii=False, indent=2)
        log_success(f"Saved: {json_file}")

    # Markdown 输出
    if output_format in ("markdown", "both"):
        md_file = output_path / f"{safe_id}.md"
        markdown_content = to_markdown(new_order)
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(markdown_content)
        log_success(f"Saved: {md_file}")

    return 0

def main():
    """主入口"""
    parser = argparse.ArgumentParser(
        description="查下订单是否充值到游戏及账号信息工具",
        epilog="支持平台: 国内、海外游戏外充值问题处理",
    )
    parser.add_argument(
        "order_id",
        nargs="?",
        help="订单号",
    )
    parser.add_argument(
        "--output", "-o",
        default="./output",
        help="输出目录 (默认: ./output)",
    )
    parser.add_argument(
        "--format", "-f",
        choices=["json", "markdown", "both"],
        default="both",
        help="输出格式 (默认: both)",
    )
    parser.add_argument(
        "--biz_type", "-b",
        choices=["国内", "海外"],
        default="国内",
        help="指定业务类型 (可选，默认国内游戏外充值)",
    )
    parser.add_argument(
        "--env", "-e",
        choices=["测试环境", "生产环境"],
        default="测试环境",
        help="指定环境 (可选，默认测试环境)",
    )

    args = parser.parse_args()

    # 执行提取
    return query_order_status(
        order_id=args.order_id,
        output_dir=args.output,
        output_format=args.format,
        biz_type=args.biz_type,
        env=args.env,
    )


if __name__ == "__main__":
    sys.exit(main())

