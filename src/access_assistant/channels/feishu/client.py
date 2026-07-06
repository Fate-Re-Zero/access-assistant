from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from .config import FeishuConfig
from .formatting import markdown_to_lark_md, split_lark_md
from .reply_feedback import (
    build_interactive_card,
    extract_message_id_from_send_response,
    store_reply_card_content,
)

log = logging.getLogger(__name__)


def resolve_receive_id_type(receive_id: str) -> str:
    if receive_id.startswith("oc_"):
        return "chat_id"
    if receive_id.startswith("ou_"):
        return "open_id"
    if receive_id.startswith("on_"):
        return "union_id"
    return "chat_id"


class FeishuApiError(RuntimeError):
    pass


class FeishuClient:
    def __init__(self, config: FeishuConfig) -> None:
        self._config = config
        self._token = ""
        self._token_expire_at = 0.0
        self._bot_open_id = config.bot_open_id.strip()

    async def reply_text(self, message_id: str, text: str, *, force_plain: bool = False) -> None:
        await self._deliver_reply(message_id, text, force_plain=force_plain)

    async def send_text_to_chat(self, receive_id: str, text: str, *, force_plain: bool = False) -> None:
        await self._deliver_to_chat(receive_id, text, force_plain=force_plain)

    async def send_progress_to_chat(self, receive_id: str, text: str) -> None:
        await self._deliver_to_chat(receive_id, text, force_plain=True)

    async def resolve_bot_open_id(self) -> str:
        if self._bot_open_id:
            return self._bot_open_id

        token = await self._get_tenant_access_token()
        url = f"{self._config.api_base}/open-apis/bot/v3/info"
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, headers=headers)
            data = response.json()

        if response.status_code >= 400 or data.get("code") not in (0, None):
            raise FeishuApiError(
                f"Feishu bot info failed: status={response.status_code}, body={data}"
            )

        bot = data.get("bot")
        if not isinstance(bot, dict):
            raise FeishuApiError("Feishu bot info missing bot payload")

        open_id = str(bot.get("open_id", "")).strip()
        if not open_id:
            raise FeishuApiError("Feishu bot info missing open_id")

        self._bot_open_id = open_id
        log.info("Resolved Feishu bot open_id=%s", open_id)
        return open_id

    async def get_user_by_open_id(self, open_id: str) -> dict[str, Any]:
        token = await self._get_tenant_access_token()
        url = (
            f"{self._config.api_base}/open-apis/contact/v3/users/{open_id}"
            f"?user_id_type=open_id"
        )
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, headers=headers)
            data = response.json()
        if response.status_code >= 400 or data.get("code") not in (0, None):
            raise FeishuApiError(
                f"Feishu user lookup failed: status={response.status_code}, body={data}"
            )
        return data.get("data") or {}

    async def download_message_file(self, message_id: str, file_key: str) -> bytes:
        token = await self._get_tenant_access_token()
        url = (
            f"{self._config.api_base}/open-apis/im/v1/messages/{message_id}/resources/{file_key}"
            f"?type=file"
        )
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, headers=headers)
        if response.status_code >= 400:
            raise FeishuApiError(
                f"Feishu file download failed: status={response.status_code}, body={response.text[:500]}"
            )
        return response.content

    async def _deliver_reply(self, message_id: str, text: str, force_plain: bool = False) -> None:
        token = await self._get_tenant_access_token()
        chunks = self._chunk_message(text, force_plain=force_plain)
        for index, chunk in enumerate(chunks):
            suffix = self._chunk_suffix(index, len(chunks))
            is_last_chunk = index == len(chunks) - 1
            payload = self._build_payload(
                chunk + suffix,
                force_plain=force_plain,
                include_feedback=is_last_chunk,
            )
            url = f"{self._config.api_base}/open-apis/im/v1/messages/{message_id}/reply"
            response_data = await self._post_json(token, url, payload)
            self._remember_feedback_card(payload, response_data, include_feedback=is_last_chunk)

    async def _deliver_to_chat(
        self,
        receive_id: str,
        text: str,
        force_plain: bool = False,
    ) -> None:
        token = await self._get_tenant_access_token()
        receive_id_type = resolve_receive_id_type(receive_id)
        chunks = self._chunk_message(text, force_plain=force_plain)
        url = (
            f"{self._config.api_base}/open-apis/im/v1/messages"
            f"?receive_id_type={receive_id_type}"
        )
        for index, chunk in enumerate(chunks):
            suffix = self._chunk_suffix(index, len(chunks))
            is_last_chunk = index == len(chunks) - 1
            payload = self._build_payload(
                chunk + suffix,
                force_plain=force_plain,
                include_feedback=is_last_chunk,
            )
            payload["receive_id"] = receive_id
            response_data = await self._post_json(token, url, payload)
            self._remember_feedback_card(payload, response_data, include_feedback=is_last_chunk)

    def _chunk_message(self, text: str, force_plain: bool = False) -> list[str]:
        use_rich = (
            not force_plain
            and self._config.use_interactive_card
            and self._config.use_lark_md
        )
        if use_rich:
            return split_lark_md(text)
        chunk_size = self._config.text_chunk_size
        normalized = (text or "").strip()
        if not normalized:
            return ["抱歉，我暂时无法生成有效回复，请稍后再试。"]
        if len(normalized) <= chunk_size:
            return [normalized]
        chunks: list[str] = []
        cursor = 0
        while cursor < len(normalized):
            chunks.append(normalized[cursor : cursor + chunk_size])
            cursor += chunk_size
        return chunks

    def _chunk_suffix(self, index: int, total: int) -> str:
        if total <= 1:
            return ""
        return f"\n\n（{index + 1}/{total}）"

    def _build_payload(
        self,
        text: str,
        force_plain: bool = False,
        *,
        include_feedback: bool = False,
    ) -> dict[str, Any]:
        if (
            not force_plain
            and self._config.use_interactive_card
            and self._config.use_lark_md
        ):
            lark_md = markdown_to_lark_md(text)
            show_feedback = (
                include_feedback
                and self._config.reply_feedback_enabled
            )
            card = build_interactive_card(
                lark_md,
                include_feedback=show_feedback,
                feedback_use_callback=self._config.reply_feedback_use_callback,
            )
            return {
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
            }

        body = markdown_to_lark_md(text) if self._config.use_lark_md and not force_plain else text
        return {
            "msg_type": "text",
            "content": json.dumps({"text": body}, ensure_ascii=False),
        }

    def _remember_feedback_card(
        self,
        payload: dict[str, Any],
        response_data: dict[str, Any],
        *,
        include_feedback: bool,
    ) -> None:
        if not include_feedback or not self._config.reply_feedback_enabled:
            return
        if payload.get("msg_type") != "interactive":
            return
        message_id = extract_message_id_from_send_response(response_data)
        if not message_id:
            return
        try:
            card = json.loads(str(payload.get("content", "{}")))
        except json.JSONDecodeError:
            return
        body = card.get("body") if isinstance(card, dict) else None
        elements = body.get("elements") if isinstance(body, dict) else None
        if not isinstance(elements, list):
            return
        for element in elements:
            if not isinstance(element, dict) or element.get("tag") != "markdown":
                continue
            content = str(element.get("content", "")).strip()
            if content:
                store_reply_card_content(message_id, content)
                return

    async def _post_json(self, token: str, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            data = response.json()
        if response.status_code >= 400 or data.get("code") not in (0, None):
            raise FeishuApiError(
                f"Feishu API failed: status={response.status_code}, body={data}"
            )
        return data if isinstance(data, dict) else {}

    async def _get_tenant_access_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_expire_at - 60:
            return self._token

        url = f"{self._config.api_base}/open-apis/auth/v3/tenant_access_token/internal"
        body = {
            "app_id": self._config.app_id,
            "app_secret": self._config.app_secret,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(url, json=body)
            data = response.json()

        if response.status_code >= 400 or data.get("code") != 0:
            raise FeishuApiError(f"Failed to fetch tenant_access_token: {data}")

        token = str(data.get("tenant_access_token", "")).strip()
        expire = int(data.get("expire", 7200))
        if not token:
            raise FeishuApiError("tenant_access_token missing in Feishu response")

        self._token = token
        self._token_expire_at = now + max(expire, 60)
        return token
