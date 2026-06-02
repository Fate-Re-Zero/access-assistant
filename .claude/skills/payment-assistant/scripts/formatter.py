# -*- coding: utf-8 -*-
"""
格式化服务 - 将 OrderInfo 转换为 Markdown
"""
from models import OrderInfo



def to_markdown(order: OrderInfo) -> str:
    """
    将 OrderInfo 转换为 Markdown 格式

    Args:
        order: 订单数据

    Returns:
        Markdown 格式的字符串
    """
    md_lines = []

    # 标题
    md_lines.append(f"# 订单{order.order_id}\n")

    # 元信息
    md_lines.append("## 文章信息\n")
    if order.order_id:
        md_lines.append(f"**订单号**: {order.order_id}  ")
    if order.app_id:
        md_lines.append(f"**应用ID**: {order.app_id}  ")
    if order.game_name:
        md_lines.append(f"**游戏名称**: {order.game_name}  ")
    if order.account_id:
        md_lines.append(f"**账号ID**: {order.account_id}  ")
    if order.pay_time:
        md_lines.append(f"**支付时间**: {order.pay_time}  ")
    md_lines.append("---\n")

    # 正文内容
    md_lines.append("## 正文内容\n")
    if order.result_message:
        md_lines.append(f"**处理结果**: {order.result_message}  ")

    return "\n".join(md_lines)
