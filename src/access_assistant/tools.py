"""
Tools 定义

使用 LangChain 的 @tool 装饰器和 ToolRuntime 定义工具：
- load_skill: 加载 Skill 详细指令
- bash: 执行命令/脚本
- read_file: 读取文件

ToolRuntime 提供访问运行时信息的统一接口：
- state: 可变的执行状态
- context: 不可变的配置（如 skill_loader）
"""

import locale
import os
import subprocess
import fnmatch
import re
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

from langchain.tools import tool, ToolRuntime

from .auth_crypto.decrypt import (
    decrypt,
    decrypt_batch,
    decrypt_json_file,
    decrypt_payload,
    format_batch_output,
)
from .skill_loader import SkillLoader
from .stream import resolve_path


@dataclass
class SkillAgentContext:
    """
    Agent 运行时上下文

    通过 ToolRuntime[SkillAgentContext] 在 tool 中访问
    """
    skill_loader: SkillLoader
    working_directory: Path = field(default_factory=Path.cwd)
    payment_agent: Any | None = None
    integration_agent: Any | None = None
    auth_agent: Any | None = None
    active_thread_id: str = ""
    loaded_skills_by_thread: dict[str, set[str]] = field(default_factory=dict)


def _decode_process_output(data: bytes | None) -> str:
    """Decode subprocess bytes on Windows where MCP/decrypt output may be UTF-8 or GBK."""
    if not data:
        return ""
    candidates = []
    for name in ("utf-8", locale.getpreferredencoding(False), "gb18030", "cp936"):
        if name and name not in candidates:
            candidates.append(name)
    for encoding in candidates:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _normalize_bash_command(command: str, working_directory: Path) -> str:
    """Drop redundant `cd access-assistant &&` when already in the project root."""
    stripped = command.strip()
    lowered = stripped.lower()
    prefix = "cd access-assistant &&"
    if lowered.startswith(prefix):
        nested = working_directory / "access-assistant"
        if working_directory.name == "access-assistant" or not nested.is_dir():
            return stripped[len(prefix) :].strip()
    return command


@tool
def load_skill(skill_name: str, runtime: ToolRuntime[SkillAgentContext]) -> str:
    """
    Load a skill's detailed instructions.

    This tool reads the SKILL.md file for the specified skill and returns
    its complete instructions. Use this when the user's request matches
    a skill's description from the available skills list.

    The skill's instructions will guide you on how to complete the task,
    which may include running scripts via the bash tool.

    Args:
        skill_name: Name of the skill to load (e.g., 'news-extractor')
    """
    loader = runtime.context.skill_loader

    skill_content = loader.load_skill(skill_name)

    if not skill_content:
        skills = loader.scan_skills()
        if skills:
            available = [s.name for s in skills]
            return f"Skill '{skill_name}' not found. Available skills: {', '.join(available)}"
        return f"Skill '{skill_name}' not found. No skills are currently available."

    skill_path = skill_content.metadata.skill_path
    scripts_dir = skill_path / "scripts"

    path_info = f"""
## Skill Path Info

- **Skill Directory**: `{skill_path}`
- **Scripts Directory**: `{scripts_dir}`

**Important**: When running scripts, use absolute paths like:
```bash
uv run {scripts_dir}/script_name.py [args]
```
"""

    return f"""# Skill: {skill_name}

## Instructions

{skill_content.instructions}
{path_info}
"""


@tool
def delegate_to_payment_agent(task: str, runtime: ToolRuntime[SkillAgentContext]) -> str:
    """
    Delegate a payment-related task to the payment sub-agent.

    Use this for:
    - 订单状态、发货、充值、账号、支付结果等支付问题
    - payment-assistant skill 相关任务

    Args:
        task: The payment task to delegate
    """
    agent = runtime.context.payment_agent
    if agent is None:
        return "[FAILED] Payment sub-agent is not configured."

    try:
        result = agent.invoke(task, thread_id=f"payment-subagent-{uuid.uuid4()}")
        response = agent.get_last_response(result).strip()
        if not response:
            return "[FAILED] Payment sub-agent returned an empty response."
        return f"[OK]\n\n{response}"
    except Exception as e:
        return f"[FAILED] {str(e)}"


@tool
def delegate_to_integration_agent(task: str, runtime: ToolRuntime[SkillAgentContext]) -> str:
    """
    Delegate an integration-related task to the integration sub-agent.

    Use this for:
    - 商户未授权、授权失败、签名错误、创建 CGW 工单（integration-support-assistant skill 范围）
    - integration-support-assistant skill 相关任务

    Do NOT use for:
    - 账号登录、账号信息/操作记录查询（auth sub-agent）
    - 回调、网关、联调、通用接口报错

    Args:
        task: The integration task to delegate
    """
    agent = runtime.context.integration_agent
    if agent is None:
        return "[FAILED] Integration sub-agent is not configured."

    try:
        result = agent.invoke(task, thread_id=f"integration-subagent-{uuid.uuid4()}")
        response = agent.get_last_response(result).strip()
        if not response:
            return "[FAILED] Integration sub-agent returned an empty response."
        return f"[OK]\n\n{response}"
    except Exception as e:
        return f"[FAILED] {str(e)}"


@tool
def delegate_to_auth_agent(task: str, runtime: ToolRuntime[SkillAgentContext]) -> str:
    """
    Delegate an auth-related task to the auth sub-agent.

    Use this for:
    - 账号登录失败、账号状态查询、账号操作记录查询
    - auth 子 skill 相关任务（auth-login-failure 等）

    Args:
        task: The auth task to delegate
    """
    agent = runtime.context.auth_agent
    if agent is None:
        return "[FAILED] Auth sub-agent is not configured."

    try:
        result = agent.invoke(task, thread_id=f"auth-subagent-{uuid.uuid4()}")
        response = agent.get_last_response(result).strip()
        if not response:
            return "[FAILED] Auth sub-agent returned an empty response."
        return f"[OK]\n\n{response}"
    except Exception as e:
        return f"[FAILED] {str(e)}"


@tool
def bash(command: str, runtime: ToolRuntime[SkillAgentContext]) -> str:
    """
    Execute a shell command (bash on Unix/macOS, cmd.exe on Windows).

    Use this for:
    - Running skill scripts (e.g., `uv run path/to/script.py args`)
    - Installing dependencies
    - File operations
    - Any shell command

    Important for Skills:
    - Script code does NOT enter the context, only the output does
    - This is Level 3 of the Skills loading mechanism
    - Follow the skill's instructions for exact command syntax

    Cross-platform Note:
    - On Unix/macOS: Uses /bin/sh (bash-compatible)
    - On Windows: Uses cmd.exe (different syntax, e.g., use 'dir' instead of 'ls')
    - For portable scripts, use Python scripts via `uv run script.py`

    Args:
        command: The shell command to execute
    """
    cwd = str(runtime.context.working_directory)
    command = _normalize_bash_command(command, runtime.context.working_directory)

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            timeout=300,
            env=_subprocess_env(),
        )
        stdout = _decode_process_output(result.stdout)
        stderr = _decode_process_output(result.stderr)

        parts = []

        if result.returncode == 0:
            parts.append("[OK]")
        else:
            parts.append(f"[FAILED] Exit code: {result.returncode}")

        parts.append("")

        if stdout:
            parts.append(stdout.rstrip())

        if stderr:
            if stdout:
                parts.append("")
            parts.append("--- stderr ---")
            parts.append(stderr.rstrip())

        if not stdout and not stderr:
            parts.append("(no output)")

        return "\n".join(parts)

    except subprocess.TimeoutExpired:
        return "[FAILED] Command timed out after 300 seconds."
    except UnicodeDecodeError as exc:
        return f"[FAILED] Could not decode command output: {exc}"
    except Exception as e:
        return f"[FAILED] {str(e)}"


@tool
def read_file(file_path: str, runtime: ToolRuntime[SkillAgentContext]) -> str:
    """
    Read the contents of a file.

    Use this to:
    - Read skill documentation files
    - View script output files
    - Inspect any text file

    Args:
        file_path: Path to the file (absolute or relative to working directory)
    """
    path = resolve_path(file_path, runtime.context.working_directory)

    if not path.exists():
        return f"[Error] File not found: {file_path}"

    if not path.is_file():
        return f"[Error] Not a file: {file_path}"

    try:
        content = path.read_text(encoding="utf-8")
        lines = content.split("\n")

        # 添加行号
        numbered_lines = []
        for i, line in enumerate(lines[:2000], 1):  # 限制行数
            numbered_lines.append(f"{i:4d}| {line}")

        if len(lines) > 2000:
            numbered_lines.append(f"... ({len(lines) - 2000} more lines)")

        return "\n".join(numbered_lines)

    except UnicodeDecodeError:
        return f"[Error] Cannot read file (binary or unknown encoding): {file_path}"
    except Exception as e:
        return f"[Error] Failed to read file: {str(e)}"


@tool
def write_file(file_path: str, content: str, runtime: ToolRuntime[SkillAgentContext]) -> str:
    """
    Write content to a file.

    Use this to:
    - Save generated content
    - Create new files
    - Modify existing files

    Args:
        file_path: Path to the file (absolute or relative to working directory)
        content: Content to write to the file
    """
    path = resolve_path(file_path, runtime.context.working_directory)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"[Success] File written: {path}"

    except Exception as e:
        return f"[Error] Failed to write file: {str(e)}"


@tool
def decrypt_mcp_result(
    runtime: ToolRuntime[SkillAgentContext],
    *,
    cipher: str = "",
    json_payload: str = "",
    json_file: str = "",
    json_files: str = "",
    labels: str = "",
    json_field: str = "result",
) -> str:
    """
    Decrypt auth-mcp-server MCP tool results (AES + GZIP).

    Use this tool after MCP calls that return encrypted payloads. Do NOT use
    bash, bare python, or Crypto/pycryptodome scripts to decrypt.

    Provide exactly one input mode per call:
    - json_payload: one MCP JSON response (preferred; decrypt as each MCP returns)
    - cipher: raw Base64 cipher string (when MCP returns plain cipher only)
    - json_file: path to a saved MCP JSON file (not used by auth agent)
    - json_files: comma-separated JSON file paths for batch decrypt (not used by auth agent)
    - labels: optional comma-separated labels for json_files (same order; default = file stem)

    Args:
        cipher: Base64 cipher text from MCP result field
        json_payload: MCP response JSON string containing encrypted result
        json_file: Single JSON file path relative to working directory
        json_files: Comma-separated JSON file paths for batch decrypt
        labels: Comma-separated labels matching json_files order
        json_field: JSON field holding cipher text (default: result)
    """
    cwd = runtime.context.working_directory
    modes = sum(
        1
        for value in (cipher.strip(), json_payload.strip(), json_file.strip(), json_files.strip())
        if value
    )
    if modes == 0:
        return (
            "[FAILED] Provide one of: cipher, json_payload, json_file, or json_files. "
            "Do not use bash/python to decrypt."
        )
    if modes > 1:
        return "[FAILED] Provide only one decrypt input mode at a time."

    try:
        if json_files.strip():
            file_paths = _split_csv(json_files)
            if not file_paths:
                return "[FAILED] json_files is empty."

            label_list = _split_csv(labels)
            if label_list and len(label_list) != len(file_paths):
                return "[FAILED] labels count must match json_files count."

            items: list[tuple[str, str]] = []
            for index, relative_path in enumerate(file_paths):
                path = resolve_path(relative_path, cwd)
                if not path.is_file():
                    return f"[FAILED] File not found: {relative_path}"
                label = label_list[index] if label_list else path.stem
                items.append((label, str(path)))

            batch_results = decrypt_batch(items, json_field=json_field)
            output = format_batch_output(batch_results)
            if any(error for _, _, error in batch_results):
                return f"[FAILED]\n\n{output}"
            return f"[OK]\n\n{output}"

        if json_file.strip():
            path = resolve_path(json_file.strip(), cwd)
            if not path.is_file():
                return f"[FAILED] File not found: {json_file}"
            plain_text = decrypt_json_file(path, json_field=json_field)
            if not plain_text:
                return "[FAILED] decrypt failed"
            return f"[OK]\n\n{plain_text}"

        if json_payload.strip():
            plain_text = decrypt_payload(json_payload.strip(), json_field)
            if not plain_text:
                return "[FAILED] decrypt failed"
            return f"[OK]\n\n{plain_text}"

        plain_text = decrypt(cipher.strip())
        if not plain_text:
            return "[FAILED] decrypt failed"
        return f"[OK]\n\n{plain_text}"

    except ValueError as exc:
        return f"[FAILED] {exc}"
    except Exception as exc:
        return f"[FAILED] {str(exc)}"


@tool
def glob(pattern: str, runtime: ToolRuntime[SkillAgentContext]) -> str:
    """
    Find files matching a glob pattern.

    Use this to:
    - Find files by name pattern (e.g., "**/*.py" for all Python files)
    - List files in a directory with wildcards
    - Discover project structure

    Args:
        pattern: Glob pattern (e.g., "**/*.py", "src/**/*.ts", "*.md")
    """
    cwd = runtime.context.working_directory

    try:
        # 使用 Path.glob 进行匹配
        matches = sorted(cwd.glob(pattern))

        if not matches:
            return f"No files matching pattern: {pattern}"

        # 限制返回数量
        max_results = 100
        result_lines = []

        for path in matches[:max_results]:
            try:
                rel_path = path.relative_to(cwd)
                result_lines.append(str(rel_path))
            except ValueError:
                result_lines.append(str(path))

        result = "\n".join(result_lines)

        if len(matches) > max_results:
            result += f"\n... and {len(matches) - max_results} more files"

        return f"[OK]\n\n{result}"

    except Exception as e:
        return f"[FAILED] {str(e)}"


@tool
def grep(pattern: str, path: str, runtime: ToolRuntime[SkillAgentContext]) -> str:
    """
    Search for a pattern in files.

    Use this to:
    - Find code containing specific text or regex
    - Search for function/class definitions
    - Locate usages of variables or imports

    Args:
        pattern: Regular expression pattern to search for
        path: File or directory path to search in (use "." for current directory)
    """
    cwd = runtime.context.working_directory
    search_path = resolve_path(path, cwd)

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"[FAILED] Invalid regex pattern: {e}"

    results = []
    max_results = 50
    files_searched = 0

    try:
        if search_path.is_file():
            files = [search_path]
        else:
            # 搜索所有文本文件，排除常见的二进制/隐藏目录
            files = []
            for p in search_path.rglob("*"):
                if p.is_file():
                    # 排除隐藏文件和常见的非代码目录
                    parts = p.parts
                    if any(part.startswith(".") or part in ("node_modules", "__pycache__", ".git", "venv", ".venv") for part in parts):
                        continue
                    files.append(p)

        for file_path in files:
            if len(results) >= max_results:
                break

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                lines = content.split("\n")
                files_searched += 1

                for line_num, line in enumerate(lines, 1):
                    if regex.search(line):
                        try:
                            rel_path = file_path.relative_to(cwd)
                        except ValueError:
                            rel_path = file_path
                        results.append(f"{rel_path}:{line_num}: {line.strip()[:100]}")

                        if len(results) >= max_results:
                            break

            except (UnicodeDecodeError, PermissionError, IsADirectoryError):
                continue

        if not results:
            return f"No matches found for pattern: {pattern} (searched {files_searched} files)"

        output = "\n".join(results)
        if len(results) >= max_results:
            output += f"\n... (truncated, showing first {max_results} matches)"

        return f"[OK]\n\n{output}"

    except Exception as e:
        return f"[FAILED] {str(e)}"


@tool
def edit(
    file_path: str,
    old_string: str,
    new_string: str,
    runtime: ToolRuntime[SkillAgentContext]
) -> str:
    """
    Edit a file by replacing text.

    Use this to:
    - Modify existing code
    - Fix bugs by replacing incorrect code
    - Update configuration values

    The old_string must match exactly (including whitespace/indentation).
    For safety, the old_string must be unique in the file.

    Args:
        file_path: Path to the file to edit
        old_string: The exact text to find and replace
        new_string: The text to replace it with
    """
    path = resolve_path(file_path, runtime.context.working_directory)

    if not path.exists():
        return f"[FAILED] File not found: {file_path}"

    if not path.is_file():
        return f"[FAILED] Not a file: {file_path}"

    try:
        content = path.read_text(encoding="utf-8")

        # 检查 old_string 是否存在
        count = content.count(old_string)

        if count == 0:
            return f"[FAILED] String not found in file. Make sure the text matches exactly including whitespace."

        if count > 1:
            return f"[FAILED] String appears {count} times in file. Please provide more context to make it unique."

        # 执行替换
        new_content = content.replace(old_string, new_string, 1)
        path.write_text(new_content, encoding="utf-8")

        # 计算变化的行数
        old_lines = len(old_string.split("\n"))
        new_lines = len(new_string.split("\n"))

        return f"[OK]\n\nEdited {path.name}: replaced {old_lines} lines with {new_lines} lines"

    except UnicodeDecodeError:
        return f"[FAILED] Cannot edit file (binary or unknown encoding): {file_path}"
    except Exception as e:
        return f"[FAILED] {str(e)}"


@tool
def list_dir(path: str, runtime: ToolRuntime[SkillAgentContext]) -> str:
    """
    List contents of a directory.

    Use this to:
    - Explore directory structure
    - See what files exist in a folder
    - Check if files/folders exist

    Args:
        path: Directory path (use "." for current directory)
    """
    dir_path = resolve_path(path, runtime.context.working_directory)

    if not dir_path.exists():
        return f"[FAILED] Directory not found: {path}"

    if not dir_path.is_dir():
        return f"[FAILED] Not a directory: {path}"

    try:
        entries = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))

        result_lines = []
        for entry in entries[:100]:  # 限制数量
            if entry.is_dir():
                result_lines.append(f"📁 {entry.name}/")
            else:
                # 显示文件大小
                size = entry.stat().st_size
                if size < 1024:
                    size_str = f"{size}B"
                elif size < 1024 * 1024:
                    size_str = f"{size // 1024}KB"
                else:
                    size_str = f"{size // (1024 * 1024)}MB"
                result_lines.append(f"   {entry.name} ({size_str})")

        if len(entries) > 100:
            result_lines.append(f"... and {len(entries) - 100} more entries")

        return f"[OK]\n\n{chr(10).join(result_lines)}"

    except PermissionError:
        return f"[FAILED] Permission denied: {path}"
    except Exception as e:
        return f"[FAILED] {str(e)}"


ALL_TOOLS = [load_skill, bash, read_file, write_file, glob, grep, edit, list_dir]
# Auth agent: skill loading and native decrypt only.
AUTH_AGENT_TOOLS = [load_skill, decrypt_mcp_result]
SUPERVISOR_TOOLS = [
    delegate_to_payment_agent,
    delegate_to_integration_agent,
    delegate_to_auth_agent,
]
