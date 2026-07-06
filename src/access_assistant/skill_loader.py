import re
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

import yaml


DEFAULT_SKILL_PATHS = [
    Path.cwd() / ".claude" / "skills",  
    Path.home() / ".claude" / "skills", 
]


@dataclass
class SkillMetadata:
    name: str               # skill 唯一名称
    description: str        # 何时使用此 skill 的描述
    skill_path: Path        # skill 目录路径
    mcp_servers: list[str] = field(default_factory=list)  # 关联的 MCP 服务名

    def to_prompt_line(self) -> str:
        """生成 system prompt 中的单行描述"""
        return f"- **{self.name}**: {self.description}"


@dataclass
class SkillContent:
    metadata: SkillMetadata
    instructions: str  # SKILL.md body 内容


def _parse_mcp_servers(raw_value: object) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        value = raw_value.strip()
        return [value] if value else []
    if isinstance(raw_value, list):
        return [str(item).strip() for item in raw_value if str(item).strip()]
    return []


class SkillLoader:
    def __init__(
        self,
        skill_paths: list[Path] | None = None,
        allowed_skill_names: list[str] | None = None,
    ):
        self.skill_paths = DEFAULT_SKILL_PATHS if skill_paths is None else skill_paths
        self._metadata_cache: dict[str, SkillMetadata] = {}
        self.allowed_skill_names = (
            {name.strip() for name in allowed_skill_names if name.strip()}
            if allowed_skill_names is not None
            else None
        )

    def _is_skill_allowed(self, skill_name: str) -> bool:
        if self.allowed_skill_names is None:
            return True
        return skill_name in self.allowed_skill_names

    def scan_skills(self) -> list[SkillMetadata]:
        skills = []
        seen_names = set()

        for base_path in self.skill_paths:
            if not base_path.exists():
                continue

            # 允许直接传入单个 skill 目录，或 skill 包目录（包根无 SKILL.md 时扫描子目录）。
            direct_skill_md = base_path / "SKILL.md"
            if base_path.is_dir() and direct_skill_md.exists():
                metadata = self._parse_skill_metadata(direct_skill_md)
                if metadata and self._is_skill_allowed(metadata.name) and metadata.name not in seen_names:
                    skills.append(metadata)
                    seen_names.add(metadata.name)
                    self._metadata_cache[metadata.name] = metadata
                continue

            if not base_path.is_dir():
                continue

            # 遍历 skills 目录下的每个子目录
            for skill_dir in base_path.iterdir():
                if not skill_dir.is_dir():
                    continue

                # 检查是否存在 SKILL.md
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue

                # 解析元数据
                metadata = self._parse_skill_metadata(skill_md)
                if metadata and self._is_skill_allowed(metadata.name) and metadata.name not in seen_names:
                    skills.append(metadata)
                    seen_names.add(metadata.name)
                    self._metadata_cache[metadata.name] = metadata

        return skills

    def _parse_skill_metadata(self, skill_md_path: Path) -> Optional[SkillMetadata]:
        try:
            content = skill_md_path.read_text(encoding="utf-8")
        except Exception:
            return None

        frontmatter_match = re.match(
            r'^---\s*\n(.*?)\n---\s*\n',
            content,
            re.DOTALL
        )

        if not frontmatter_match:
            return None

        try:
            # 解析 YAML
            frontmatter = yaml.safe_load(frontmatter_match.group(1))

            name = frontmatter.get("name", "")
            description = frontmatter.get("description", "")
            mcp_servers = _parse_mcp_servers(frontmatter.get("mcp_servers"))

            if not name:
                return None

            return SkillMetadata(
                name=name,
                description=description,
                skill_path=skill_md_path.parent,
                mcp_servers=mcp_servers,
            )
        except yaml.YAMLError:
            return None

    def load_skill(self, skill_name: str) -> Optional[SkillContent]:
        if not self._is_skill_allowed(skill_name):
            return None

        # 先检查缓存
        metadata = self._metadata_cache.get(skill_name)
        if not metadata:
            # 尝试重新扫描
            self.scan_skills()
            metadata = self._metadata_cache.get(skill_name)

        if not metadata:
            return None

        # 读取 SKILL.md 完整内容
        skill_md = metadata.skill_path / "SKILL.md"
        try:
            content = skill_md.read_text(encoding="utf-8")
        except Exception:
            return None

        # 提取 body（去除 frontmatter）
        body_match = re.match(
            r'^---\s*\n.*?\n---\s*\n(.*)$',
            content,
            re.DOTALL
        )
        instructions = body_match.group(1).strip() if body_match else content

        # 只返回 instructions，让大模型从指令中自己发现脚本和文档
        return SkillContent(
            metadata=metadata,
            instructions=instructions,
        )

    def build_system_prompt(self, base_prompt: str = "") -> str:
        skills = self.scan_skills()

        # 构建 Skills 部分
        if skills:
            skills_section = "## Available Skills\n\n"
            skills_section += "You have access to the following specialized skills:\n\n"
            for skill in skills:
                skills_section += skill.to_prompt_line() + "\n"
            skills_section += "\n"
            skills_section += "### How to Use Skills\n\n"
            skills_section += "1. **Discover**: Review the skills list above\n"
            skills_section += "2. **Load**: When a user request matches a skill's description, "
            skills_section += "use `load_skill(skill_name)` to get detailed instructions\n"
            skills_section += "3. **Execute**: Follow the skill's instructions, which may include "
            skills_section += "running scripts via `bash`\n\n"
            skills_section += "**Important**: Only load a skill when it's relevant to the user's request. "
            skills_section += "Script code never enters the context - only their output does.\n"
        else:
            skills_section = "## Skills\n\nNo skills currently available.\n"

        # 组合完整 prompt
        if base_prompt:
            return f"{base_prompt}\n\n{skills_section}"
        else:
            return f"You are a helpful Payment Assistant. When the user asks in Chinese, answer in Chinese.\n\n{skills_section}"


# 便捷函数
def discover_skills(skill_paths: list[Path] | None = None) -> list[SkillMetadata]:
    """发现所有 Skills"""
    loader = SkillLoader(skill_paths)
    return loader.scan_skills()


def get_skill_content(skill_name: str, skill_paths: list[Path] | None = None) -> Optional[SkillContent]:
    """获取 Skill 内容"""
    loader = SkillLoader(skill_paths)
    return loader.load_skill(skill_name)
