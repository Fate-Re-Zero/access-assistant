from __future__ import annotations

from access_assistant.agent import _normalize_openai_base_url


def test_normalize_openai_base_url_appends_v1_for_standard_proxy():
    assert _normalize_openai_base_url("https://api.jiekou.ai/openai") == (
        "https://api.jiekou.ai/openai/v1"
    )
    assert _normalize_openai_base_url("https://api.deepseek.com") == (
        "https://api.deepseek.com/v1"
    )


def test_normalize_openai_base_url_keeps_existing_v1():
    assert _normalize_openai_base_url("https://npai.u.sdo.com/v1") == (
        "https://npai.u.sdo.com/v1"
    )


def test_normalize_openai_base_url_keeps_maxkb_resource_path():
    base = "https://maxkb.sdo.com/chat/api/019e882e-4900-7aa1-bfd0-632104271b64"
    assert _normalize_openai_base_url(base) == base


def test_normalize_openai_base_url_strips_full_endpoint_before_normalize():
    base = "https://maxkb.sdo.com/chat/api/019e882e-4900-7aa1-bfd0-632104271b64"
    assert _normalize_openai_base_url(f"{base}/chat/completions") == base
