from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from .client import FeishuClient
from .config import FeishuConfig

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeishuUserIdentity:
    open_id: str
    name: str
    email: str
    enterprise_email: str
    is_resigned: bool
    is_frozen: bool


class FeishuIdentityService:
    """Resolve Feishu open_id to tenant user profile and enforce SSO rules."""

    def __init__(self, config: FeishuConfig, client: FeishuClient) -> None:
        self._config = config
        self._client = client
        self._cache: dict[str, tuple[float, FeishuUserIdentity | None]] = {}

    async def verify(self, open_id: str) -> tuple[bool, FeishuUserIdentity | None, str]:
        if not self._config.sso_enabled:
            return True, None, ""

        identity = await self.resolve(open_id)
        if identity is None:
            return False, None, "无法验证企业身份，请联系管理员开通飞书通讯录权限。"

        if identity.is_resigned:
            return False, identity, "当前账号已离职，无法使用机器人。"

        if identity.is_frozen:
            return False, identity, "当前账号已冻结，无法使用机器人。"

        email = identity.enterprise_email or identity.email
        if self._config.sso_allowed_email_domains:
            domain = email.split("@")[-1].lower() if "@" in email else ""
            if not domain or domain not in self._config.sso_allowed_email_domains:
                return False, identity, "当前账号不在允许的企业邮箱域内。"

        return True, identity, ""

    async def resolve(self, open_id: str) -> FeishuUserIdentity | None:
        normalized = open_id.strip()
        if not normalized:
            return None

        now = time.time()
        cached = self._cache.get(normalized)
        if cached and now - cached[0] < self._config.sso_cache_ttl_seconds:
            return cached[1]

        try:
            payload = await self._client.get_user_by_open_id(normalized)
            identity = self._parse_user(normalized, payload)
        except Exception as exc:
            log.warning("Feishu identity lookup failed: open_id=%s error=%s", normalized, exc)
            identity = None

        self._cache[normalized] = (now, identity)
        return identity

    def _parse_user(self, open_id: str, payload: dict[str, Any]) -> FeishuUserIdentity | None:
        user = payload.get("user")
        if not isinstance(user, dict):
            return None

        status = user.get("status") if isinstance(user.get("status"), dict) else {}
        return FeishuUserIdentity(
            open_id=open_id,
            name=str(user.get("name") or user.get("nickname") or "").strip(),
            email=str(user.get("email") or "").strip(),
            enterprise_email=str(user.get("enterprise_email") or "").strip(),
            is_resigned=bool(status.get("is_resigned")),
            is_frozen=bool(status.get("is_frozen")),
        )
