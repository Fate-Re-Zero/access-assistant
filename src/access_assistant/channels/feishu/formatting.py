from __future__ import annotations

import re

LARK_MD_MAX = 2800


def markdown_to_lark_md(text: str) -> str:
    """Convert common Markdown to Feishu lark_md-friendly text."""
    normalized = (text or "").strip()
    if not normalized:
        return ""

    lines = normalized.splitlines()
    output: list[str] = []
    in_code_block = False

    for line in lines:
        stripped = line.rstrip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            output.append(stripped)
            continue

        if in_code_block:
            output.append(stripped)
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            title = heading.group(2).strip()
            if title:
                output.append(f"**{title}**")
            continue

        bullet = re.match(r"^[\-*]\s+(.*)$", stripped)
        if bullet:
            output.append(f"- {bullet.group(1).strip()}")
            continue

        ordered = re.match(r"^\d+\.\s+(.*)$", stripped)
        if ordered:
            output.append(f"- {ordered.group(1).strip()}")
            continue

        output.append(stripped)

    result = "\n".join(output).strip()
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


def split_lark_md(text: str, chunk_size: int = LARK_MD_MAX) -> list[str]:
    normalized = markdown_to_lark_md(text)
    if not normalized:
        return [""]
    if len(normalized) <= chunk_size:
        return [normalized]

    chunks: list[str] = []
    cursor = 0
    while cursor < len(normalized):
        chunks.append(normalized[cursor : cursor + chunk_size])
        cursor += chunk_size
    return chunks
