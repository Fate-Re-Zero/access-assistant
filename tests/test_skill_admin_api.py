from __future__ import annotations

import io
import sys
import types
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1] / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_pkg = types.ModuleType("access_assistant")
_pkg.__path__ = [str(ROOT / "access_assistant")]
sys.modules.setdefault("access_assistant", _pkg)

from access_assistant.admin_api import create_admin_router  # noqa: E402
from access_assistant.skill_deploy import deploy_skill_package, get_deploy_status  # noqa: E402


@pytest.fixture
def skills_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setenv("SKILLS_ROOT", str(root))
    monkeypatch.setenv("ADMIN_INTERNAL_TOKEN", "test-admin-token")
    return root


@pytest.fixture
def admin_client() -> TestClient:
    app = FastAPI()
    app.include_router(create_admin_router())
    return TestClient(app)


def _build_zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_deploy_skill_package_extracts_nested_directory(skills_root: Path) -> None:
    payload = _build_zip(
        {
            "demo-skill/SKILL.md": "---\nname: demo-skill\ndescription: Demo\n---\n\nBody\n",
            "demo-skill/scripts/run.sh": "#!/bin/sh\necho ok\n",
        }
    )

    result = deploy_skill_package("demo-skill", payload, skills_root=skills_root)

    assert result["deployed_file_count"] == 2
    assert (skills_root / "demo-skill" / "SKILL.md").is_file()
    assert (skills_root / "demo-skill" / "scripts" / "run.sh").is_file()
    assert result["deploy_path"] == str(skills_root / "demo-skill")


def test_admin_deploy_requires_token(admin_client: TestClient, skills_root: Path) -> None:
    payload = _build_zip({"SKILL.md": "---\nname: plain\ndescription: Plain\n---\n"})
    response = admin_client.post(
        "/api/admin/skills/plain/deploy",
        files={"file": ("plain.zip", payload, "application/zip")},
    )
    assert response.status_code == 401

    response = admin_client.post(
        "/api/admin/skills/plain/deploy",
        headers={"Authorization": "Bearer test-admin-token"},
        content=payload,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["slug"] == "plain"
    assert body["deployed_file_count"] == 1
    assert (skills_root / "plain" / "SKILL.md").is_file()


def test_admin_update_and_delete_skill_md(admin_client: TestClient, skills_root: Path) -> None:
    headers = {"Authorization": "Bearer test-admin-token"}
    create = admin_client.put(
        "/api/admin/skills/meta/skill-md",
        headers=headers,
        json={"content": "# Meta only\n"},
    )
    assert create.status_code == 200
    assert (skills_root / "meta" / "SKILL.md").read_text(encoding="utf-8") == "# Meta only\n"

    delete = admin_client.delete("/api/admin/skills/meta", headers=headers)
    assert delete.status_code == 200
    assert delete.json()["removed"] is True
    assert not (skills_root / "meta").exists()


def test_admin_status_lists_deployed_skills(admin_client: TestClient, skills_root: Path) -> None:
    deploy_skill_package(
        "listed",
        _build_zip({"SKILL.md": "---\nname: listed\ndescription: Listed\n---\n"}),
        skills_root=skills_root,
    )
    response = admin_client.get(
        "/api/admin/skills/status",
        headers={"Authorization": "Bearer test-admin-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["skills_root"] == str(skills_root)
    assert any(item["slug"] == "listed" for item in body["skills"])
    assert get_deploy_status(skills_root=skills_root)["skills"]
