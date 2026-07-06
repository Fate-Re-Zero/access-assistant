from access_assistant.auth_direct_scope import (
    AUTH_DIRECT_OUT_OF_SCOPE_REPLY,
    build_auth_direct_scoped_input,
)


def test_build_auth_direct_scoped_input_wraps_user_message() -> None:
    user_message = "18877179115 达到实名认证操作上限 增加次数"
    wrapped = build_auth_direct_scoped_input(user_message)

    assert user_message in wrapped
    assert "增加实名认证次数上限" in wrapped
    assert "操作上限增加次数" in wrapped
    assert AUTH_DIRECT_OUT_OF_SCOPE_REPLY in wrapped
    assert "不要调用 MCP 工具" in wrapped
