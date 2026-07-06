from __future__ import annotations

import asyncio

from access_assistant.channels.feishu.file_intent import (
    FileIntentClassifier,
    _parse_classifier_response,
    mentions_file_keywords,
    should_await_file_upload,
)

KEYWORDS = frozenset({"文件", "文档", "附件", "报告"})


def test_mentions_file_keywords():
    assert mentions_file_keywords("请帮我分析这个文档", KEYWORDS) is True
    assert mentions_file_keywords("这份报告有问题", KEYWORDS) is True
    assert mentions_file_keywords("VIP等级是多少", KEYWORDS) is False
    assert mentions_file_keywords("", KEYWORDS) is False


def test_parse_classifier_response():
    assert _parse_classifier_response('{"wants_file_processing": true}') is True
    assert _parse_classifier_response('{"wants_file_processing": false}') is False
    assert _parse_classifier_response("说明：{\"wants_file_processing\": true}") is True
    assert _parse_classifier_response("garbage") is None


def test_should_await_file_upload_keyword_only():
    async def run() -> bool:
        return await should_await_file_upload(
            "请总结这份文档",
            keywords=KEYWORDS,
            classifier=None,
        )

    assert asyncio.run(run()) is True

    async def run_no_keyword() -> bool:
        return await should_await_file_upload(
            "VIP等级是多少",
            keywords=KEYWORDS,
            classifier=None,
        )

    assert asyncio.run(run_no_keyword()) is False


def test_should_await_file_upload_with_llm_disabled_classifier(monkeypatch):
    classifier = FileIntentClassifier(llm_enabled=False, timeout_seconds=1.0)

    async def run() -> bool:
        return await should_await_file_upload(
            "请分析附件",
            keywords=KEYWORDS,
            classifier=classifier,
        )

    assert asyncio.run(run()) is True


def test_classifier_llm_positive(monkeypatch):
    classifier = FileIntentClassifier(llm_enabled=True, timeout_seconds=1.0)

    class FakeModel:
        def invoke(self, _messages):
            class Resp:
                content = '{"wants_file_processing": true}'

            return Resp()

    monkeypatch.setattr(classifier, "_get_model", lambda: FakeModel())

    async def run() -> bool:
        return await classifier.has_file_processing_intent("请总结这份报告")

    assert asyncio.run(run()) is True


def test_classifier_llm_negative(monkeypatch):
    classifier = FileIntentClassifier(llm_enabled=True, timeout_seconds=1.0)

    class FakeModel:
        def invoke(self, _messages):
            class Resp:
                content = '{"wants_file_processing": false}'

            return Resp()

    monkeypatch.setattr(classifier, "_get_model", lambda: FakeModel())

    async def run() -> bool:
        return await classifier.has_file_processing_intent("文件传输协议是什么")

    assert asyncio.run(run()) is False
