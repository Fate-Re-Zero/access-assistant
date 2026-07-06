from __future__ import annotations

AUTH_DIRECT_OUT_OF_SCOPE_REPLY = "暂时无权限使用处理其他问题的能力。"

_AUTH_DIRECT_SCOPE_PREFIX = f"""【Auth 专用入口服务范围限制】
你只能处理以下类型的问题：
- 增加实名认证次数上限
- 操作上限增加次数

若用户问题不属于上述类型，请直接回复：{AUTH_DIRECT_OUT_OF_SCOPE_REPLY}
不要调用 MCP 工具或 load_skill 排查其他类型的问题。

用户问题：
"""


def build_auth_direct_scoped_input(user_message: str) -> str:
    """Wrap user input with scope restrictions for dedicated Auth entry points."""
    return f"{_AUTH_DIRECT_SCOPE_PREFIX}{user_message}"
