from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .skill_deploy import (
    SkillDeployError,
    delete_skill,
    deploy_skill_package,
    get_deploy_status,
    update_skill_markdown,
)


class SkillMarkdownUpdateRequest(BaseModel):
    content: str = Field(..., min_length=0)


def _admin_token() -> str:
    return (os.getenv("ADMIN_INTERNAL_TOKEN") or os.getenv("ACCESS_ASSISTANT_ADMIN_TOKEN") or "").strip()


def verify_admin_token(authorization: str | None = None) -> None:
    expected = _admin_token()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Admin API disabled: ADMIN_INTERNAL_TOKEN is not configured",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing admin authorization token")
    token = authorization[len("Bearer ") :].strip()
    if token != expected:
        raise HTTPException(status_code=403, detail="Invalid admin authorization token")


def create_admin_router() -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["admin"])

    def admin_auth(authorization: str | None = Header(default=None)) -> None:
        verify_admin_token(authorization)

    @router.get("/skills/status")
    def skills_status(_: None = Depends(admin_auth)) -> dict[str, Any]:
        return get_deploy_status()

    @router.post("/skills/{slug}/deploy")
    async def deploy_skill(
        slug: str,
        request: Request,
        _: None = Depends(admin_auth),
    ) -> dict[str, Any]:
        payload = await request.body()
        if not payload:
            raise HTTPException(status_code=400, detail="Skill 压缩包为空")
        try:
            return deploy_skill_package(slug, payload)
        except SkillDeployError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.put("/skills/{slug}/skill-md")
    def update_skill_md(
        slug: str,
        request: SkillMarkdownUpdateRequest,
        _: None = Depends(admin_auth),
    ) -> dict[str, Any]:
        try:
            return update_skill_markdown(slug, request.content)
        except SkillDeployError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/skills/{slug}")
    def remove_skill(slug: str, _: None = Depends(admin_auth)) -> dict[str, Any]:
        try:
            return delete_skill(slug)
        except SkillDeployError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
