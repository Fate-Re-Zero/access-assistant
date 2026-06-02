# -*- coding: utf-8 -*-
"""
数据模型定义
"""
from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, Field

class OrderInfo(BaseModel):
    """Canonical representation of a order."""

    order_id: str = Field(default="")
    app_id: str = Field(default="")
    game_name: str = Field(default="")
    account_id: str = Field(default="")
    pay_time: str = Field(default="")
    result_message: str = Field(default="")
    uuid: str = Field(default="")

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain dictionary representation."""
        return self.model_dump(exclude_none=True)
