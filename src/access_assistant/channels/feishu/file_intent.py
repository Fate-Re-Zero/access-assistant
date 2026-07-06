from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage

from access_assistant.agent import _normalize_openai_base_url

log = logging.getLogger(__name__)

_DEFAULT_KEYWORDS = frozenset({"文件", "文档", "附件", "报告"})

_CLASSIFIER_PROMPT = """判断用户是否希望上传文件/文档/附件/报告，并由机器人对文件内容进行处理（例如：总结、分析、提取、翻译、润色、检查、对比、归纳等）。

仅当用户明确表达对「某份文件内容」的处理需求时，回答 wants_file_processing=true。
若只是闲聊、查询业务数据、或虽提到「文件/文档」但与上传并处理文件无关，回答 false。

用户消息：
\"\"\"{text}\"\"\"

只输出一行 JSON，不要其它文字：{{"wants_file_processing": true}} 或 {{"wants_file_processing": false}}
"""


def mentions_file_keywords(text: str, keywords: frozenset[str]) -> bool:
    """Stage-1: configurable keyword gate before LLM intent classification."""
    normalized = (text or "").strip()
    if not normalized:
        return False
    hints = keywords or _DEFAULT_KEYWORDS
    lowered = normalized.lower()
    return any(
        hint.strip() and (hint.lower() in lowered or hint in normalized)
        for hint in hints
    )


def _parse_classifier_response(raw: str) -> bool | None:
    cleaned = (raw or "").strip()
    if not cleaned:
        return None

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict) and "wants_file_processing" in parsed:
            return bool(parsed["wants_file_processing"])
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[^{}]*wants_file_processing[^{}]*\}", cleaned, flags=re.IGNORECASE)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict) and "wants_file_processing" in parsed:
                return bool(parsed["wants_file_processing"])
        except json.JSONDecodeError:
            pass

    lowered = cleaned.lower()
    if '"wants_file_processing": true' in lowered or '"wants_file_processing":true' in lowered:
        return True
    if '"wants_file_processing": false' in lowered or '"wants_file_processing":false' in lowered:
        return False
    return None


def _resolve_classifier_model_config() -> tuple[str, str, str | None, str | None]:
    model = (
        (os.getenv("LIGHTWEIGHT_MODEL") or os.getenv("MODEL_NAME") or "gpt-5-mini").strip()
    )
    provider = (
        (os.getenv("LIGHTWEIGHT_MODEL_PROVIDER") or os.getenv("MODEL_PROVIDER") or "openai").strip()
    )
    api_key = (
        os.getenv("LIGHTWEIGHT_API_KEY")
        or os.getenv("MODEL_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or ""
    ).strip() or None
    base_url = (
        os.getenv("LIGHTWEIGHT_BASE_URL")
        or os.getenv("MODEL_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or os.getenv("DEEPSEEK_BASE_URL")
        or ""
    ).strip() or None
    return model, provider, api_key, base_url


class FileIntentClassifier:
    """Stage-2: lightweight LLM to confirm file-processing intent."""

    def __init__(
        self,
        *,
        llm_enabled: bool = True,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._llm_enabled = llm_enabled
        self._timeout_seconds = timeout_seconds
        self._model: Any | None = None

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model

        model_name, provider, api_key, base_url = _resolve_classifier_model_config()
        init_kwargs: dict[str, Any] = {
            "model_provider": provider,
            "temperature": 0,
            "max_tokens": 64,
        }
        if api_key:
            init_kwargs["api_key"] = api_key
        if base_url and provider == "openai":
            init_kwargs["base_url"] = _normalize_openai_base_url(base_url)

        self._model = init_chat_model(model_name, **init_kwargs)
        log.info(
            "Feishu file intent classifier ready: model=%s provider=%s",
            model_name,
            provider,
        )
        return self._model

    def _classify_sync(self, text: str) -> bool:
        model = self._get_model()
        prompt = _CLASSIFIER_PROMPT.format(text=text.strip())
        response = model.invoke([HumanMessage(content=prompt)])
        content = response.content if hasattr(response, "content") else str(response)
        parsed = _parse_classifier_response(str(content))
        if parsed is None:
            log.warning(
                "File intent LLM unparseable response: %s",
                str(content)[:300],
            )
            return False
        return parsed

    async def has_file_processing_intent(self, text: str) -> bool:
        if not self._llm_enabled:
            return True

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self._classify_sync, text),
                timeout=self._timeout_seconds,
            )
            log.info(
                "File intent LLM: wants_file_processing=%s text=%r",
                result,
                text[:120],
            )
            return result
        except TimeoutError:
            log.warning("File intent LLM timed out after %.1fs", self._timeout_seconds)
            return False
        except Exception as exc:
            log.warning("File intent LLM failed: %s", exc)
            return False


async def should_await_file_upload(
    text: str,
    *,
    keywords: frozenset[str],
    classifier: FileIntentClassifier | None,
) -> bool:
    """Two-stage gate for ask-first-then-file flow."""
    if not mentions_file_keywords(text, keywords):
        return False
    if classifier is None:
        log.info("File intent keyword matched, LLM classifier disabled: text=%r", text[:120])
        return True
    return await classifier.has_file_processing_intent(text)
