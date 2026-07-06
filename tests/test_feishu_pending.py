from __future__ import annotations

import time

from access_assistant.channels.feishu.file_intent import mentions_file_keywords
from access_assistant.channels.feishu.pending import FeishuPendingStore, PendingFile, PendingQuestion

KEYWORDS = frozenset({"文件", "文档", "附件", "报告"})


def test_pending_store_file_and_question():
    store = FeishuPendingStore(ttl_seconds=60.0)
    pending_file = PendingFile(
        file_name="a.md",
        file_content="hello",
        truncated=False,
        source_message_id="om_1",
    )
    store.set_file("oc_1", "ou_1", pending_file)
    assert store.get_file("oc_1", "ou_1") == pending_file
    assert store.has_pending("oc_1", "ou_1") is True

    store.set_question("oc_1", "ou_1", PendingQuestion(text="请总结"))
    assert store.get_question("oc_1", "ou_1") is not None
    assert store.get_file("oc_1", "ou_1") == pending_file


def test_pending_store_ttl_expires():
    store = FeishuPendingStore(ttl_seconds=0.01)
    store.set_file(
        "oc_1",
        "ou_1",
        PendingFile(
            file_name="a.txt",
            file_content="x",
            truncated=False,
            source_message_id="om_1",
        ),
    )
    time.sleep(0.02)
    assert store.get_file("oc_1", "ou_1") is None
    assert store.has_pending("oc_1", "ou_1") is False


def test_pending_store_clear():
    store = FeishuPendingStore(ttl_seconds=60.0)
    store.set_question("oc_1", "ou_1", PendingQuestion(text="question"))
    store.clear("oc_1", "ou_1")
    assert store.has_pending("oc_1", "ou_1") is False


def test_mentions_file_keywords_for_intent_gate():
    assert mentions_file_keywords("请帮我分析这个文档", KEYWORDS) is True
    assert mentions_file_keywords("VIP等级是多少", KEYWORDS) is False
