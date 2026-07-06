from access_assistant.channels.feishu.auth_webhook_log import (
    AUTH_WEBHOOK_LOG_PREFIX,
    log_auth_access_check,
    log_auth_mention_check,
    log_auth_webhook,
    log_auth_whitelist_check,
    mention_open_ids,
)


def test_log_auth_access_rejects_p2p_when_disabled() -> None:
    assert log_auth_access_check(
        chat_id="oc_any",
        chat_type="p2p",
        allowed_chat_ids=frozenset({"oc_allowed"}),
        p2p_enabled=False,
    ) is False


def test_log_auth_whitelist_allows_p2p() -> None:
    assert log_auth_whitelist_check(
        chat_id="oc_any",
        chat_type="p2p",
        allowed_chat_ids=frozenset({"oc_allowed"}),
    ) is True


def test_log_auth_whitelist_rejects_unknown_group() -> None:
    assert log_auth_whitelist_check(
        chat_id="oc_other",
        chat_type="group",
        allowed_chat_ids=frozenset({"oc_allowed"}),
    ) is False


def test_mention_open_ids_extracts_values() -> None:
    from access_assistant.channels.feishu.events import FeishuTextMessage

    message = FeishuTextMessage(
        event_id="evt",
        message_id="om",
        chat_id="oc_group",
        open_id="ou_user",
        text="@bot hello",
        chat_type="group",
        mentions=(
            {
                "key": "@_user_1",
                "id": {"open_id": "ou_bot"},
                "name": "Auth Bot",
            },
        ),
    )
    assert mention_open_ids(message) == ["ou_bot"]
    assert AUTH_WEBHOOK_LOG_PREFIX == "[AUTH-WEBHOOK]"
