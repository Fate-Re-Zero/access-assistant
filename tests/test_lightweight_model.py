from __future__ import annotations

import os

from access_assistant.multi_agent import _resolve_lightweight_model_overrides


def test_lightweight_overrides_disabled_by_default():
    os.environ.pop("LIGHTWEIGHT_MODEL", None)
    model, provider, api_key, base_url, enabled = _resolve_lightweight_model_overrides(
        "gpt-5.4",
        "openai",
    )
    assert enabled is False
    assert model == "gpt-5.4"
    assert provider == "openai"
    assert api_key is None
    assert base_url is None


def test_lightweight_overrides_from_env(monkeypatch):
    monkeypatch.setenv("LIGHTWEIGHT_MODEL", "gpt-5-mini")
    monkeypatch.setenv("LIGHTWEIGHT_MODEL_PROVIDER", "openai")
    monkeypatch.setenv("LIGHTWEIGHT_API_KEY", "lw-key")
    monkeypatch.setenv("LIGHTWEIGHT_BASE_URL", "https://example.com/v1")

    model, provider, api_key, base_url, enabled = _resolve_lightweight_model_overrides(
        "gpt-5.4",
        "openai",
    )
    assert enabled is True
    assert model == "gpt-5-mini"
    assert provider == "openai"
    assert api_key == "lw-key"
    assert base_url == "https://example.com/v1"
