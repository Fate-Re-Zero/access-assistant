from __future__ import annotations

import io
import logging
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAX_ZIP_ENTRIES = 2000
MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
SLUG_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


class SkillDeployError(Exception):
    """Raised when skill package deploy fails."""


def resolve_skills_root() -> Path:
    configured = (os.getenv("SKILLS_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.cwd() / ".claude" / "skills").resolve()


def validate_slug(slug: str) -> str:
    value = (slug or "").strip()
    if not value or not SLUG_PATTERN.fullmatch(value):
        raise SkillDeployError(f"非法 Skill slug: {slug}")
    return value


def _normalize_entry_name(entry_name: str) -> str:
    normalized = entry_name.replace("\\", "/")
    while normalized.startswith("/"):
        normalized = normalized[1:]
    return normalized


def _should_skip_entry(entry_name: str) -> bool:
    normalized = entry_name.replace("\\", "/")
    return (
        normalized.startswith("__MACOSX/")
        or normalized.endswith("/.DS_Store")
        or normalized == ".DS_Store"
    )


def _resolve_skill_root(temp_root: Path) -> Path:
    direct_skill_md = temp_root / "SKILL.md"
    if direct_skill_md.is_file():
        return temp_root

    candidates = [
        path
        for path in temp_root.iterdir()
        if path.is_dir() and not _should_skip_entry(path.name) and (path / "SKILL.md").is_file()
    ]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise SkillDeployError("压缩包包含多个 Skill 目录，请每次仅上传一个 Skill")
    raise SkillDeployError("压缩包中未找到 SKILL.md，请确认目录结构正确")


def _extract_zip(zip_bytes: bytes, target_dir: Path) -> None:
    total_uncompressed = 0
    entry_count = 0
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            entry_count += 1
            if entry_count > MAX_ZIP_ENTRIES:
                raise SkillDeployError("Skill 压缩包文件条目过多")

            entry_name = _normalize_entry_name(info.filename)
            if _should_skip_entry(entry_name):
                continue

            destination = (target_dir / entry_name).resolve()
            if not str(destination).startswith(str(target_dir.resolve())):
                raise SkillDeployError(f"Skill 压缩包包含非法路径: {info.filename}")

            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, destination.open("wb") as dest:
                while True:
                    chunk = source.read(8192)
                    if not chunk:
                        break
                    dest.write(chunk)
                    total_uncompressed += len(chunk)
                    if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                        raise SkillDeployError("Skill 压缩包解压后体积过大")

    if entry_count == 0:
        raise SkillDeployError("Skill 压缩包为空")


def _copy_tree(source: Path, target: Path) -> int:
    count = 0
    for path in source.rglob("*"):
        if path.is_file():
            relative = path.relative_to(source)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            count += 1
    return count


def deploy_skill_package(slug: str, zip_bytes: bytes, skills_root: Path | None = None) -> dict[str, Any]:
    """Extract zip and atomically replace {skills_root}/{slug}/."""
    validated_slug = validate_slug(slug)
    root = (skills_root or resolve_skills_root()).resolve()
    root.mkdir(parents=True, exist_ok=True)

    target_dir = (root / validated_slug).resolve()
    if not str(target_dir).startswith(str(root)):
        raise SkillDeployError(f"非法 Skill slug: {slug}")

    temp_root = Path(tempfile.mkdtemp(prefix="skill-deploy-"))
    staging_dir = temp_root / "staging"
    try:
        _extract_zip(zip_bytes, temp_root / "extract")
        skill_root = _resolve_skill_root(temp_root / "extract")
        staging_dir.mkdir(parents=True, exist_ok=True)
        deployed_file_count = _copy_tree(skill_root, staging_dir)

        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(staging_dir, target_dir)

        deploy_path = str(target_dir)
        log.info(
            "Deployed skill package '%s' (%s files) to %s",
            validated_slug,
            deployed_file_count,
            deploy_path,
        )
        return {
            "slug": validated_slug,
            "deploy_path": deploy_path,
            "deployed_file_count": deployed_file_count,
            "skills_root": str(root),
        }
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def update_skill_markdown(slug: str, content: str, skills_root: Path | None = None) -> dict[str, Any]:
    validated_slug = validate_slug(slug)
    root = (skills_root or resolve_skills_root()).resolve()
    skill_dir = (root / validated_slug).resolve()
    if not str(skill_dir).startswith(str(root)):
        raise SkillDeployError(f"非法 Skill slug: {slug}")

    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(content or "", encoding="utf-8")
    log.info("Updated SKILL.md for '%s' at %s", validated_slug, skill_md)
    return {
        "slug": validated_slug,
        "deploy_path": str(skill_dir),
        "skill_md_path": str(skill_md),
        "skills_root": str(root),
    }


def delete_skill(slug: str, skills_root: Path | None = None) -> dict[str, Any]:
    validated_slug = validate_slug(slug)
    root = (skills_root or resolve_skills_root()).resolve()
    skill_dir = (root / validated_slug).resolve()
    if not str(skill_dir).startswith(str(root)):
        raise SkillDeployError(f"非法 Skill slug: {slug}")

    removed = False
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
        removed = True
        log.info("Removed skill directory '%s'", skill_dir)

    return {
        "slug": validated_slug,
        "removed": removed,
        "skills_root": str(root),
    }


def get_deploy_status(skills_root: Path | None = None) -> dict[str, Any]:
    root = (skills_root or resolve_skills_root()).resolve()
    skills: list[dict[str, str]] = []
    if root.exists():
        for path in sorted(root.iterdir()):
            if path.is_dir() and (path / "SKILL.md").is_file():
                skills.append({"slug": path.name, "path": str(path)})
    return {
        "skills_root": str(root),
        "skills": skills,
    }
