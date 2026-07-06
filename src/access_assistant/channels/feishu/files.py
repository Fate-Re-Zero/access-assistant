from __future__ import annotations

from pathlib import PurePath


def normalize_extension(file_name: str) -> str:
    return PurePath(file_name or "").suffix.lower()


def is_allowed_text_file(file_name: str, allowed_extensions: frozenset[str]) -> bool:
    extension = normalize_extension(file_name)
    if not extension:
        return False
    return extension in allowed_extensions


def decode_text_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("unsupported text encoding")


def truncate_for_prompt(text: str, max_chars: int) -> tuple[str, bool]:
    normalized = (text or "").strip()
    if max_chars <= 0 or len(normalized) <= max_chars:
        return normalized, False
    return normalized[:max_chars].rstrip(), True


def build_file_agent_prompt(
    *,
    file_name: str,
    file_content: str,
    user_text: str = "",
    truncated: bool = False,
) -> str:
    parts = [f"[用户上传文件: {file_name}]"]
    caption = (user_text or "").strip()
    if caption:
        parts.append(f"用户附言: {caption}")
    parts.append("---")
    parts.append("文件内容:")
    parts.append(file_content)
    if truncated:
        parts.append("---")
        parts.append("[注: 文件内容过长，已截断后供分析]")
    if caption:
        parts.append("请根据以上文件内容及附言回答用户问题。")
    else:
        parts.append("请阅读以上文件内容并回答；如有需要可先简要总结。")
    return "\n".join(parts)


def format_allowed_extensions(allowed_extensions: frozenset[str]) -> str:
    return ", ".join(sorted(ext.lstrip(".") for ext in allowed_extensions))
