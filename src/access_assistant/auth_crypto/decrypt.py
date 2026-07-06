"""Auth MCP AES+GZIP decrypt helpers."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .aes_cipher import SimpleAESCipher
from .compression import compress_to_base64, decompress_from_base64

AES_ENCRYPT_KEY = "Ae9fo1pFx90a$9d1Ef1Vc4Id890pQ2Md"
_aes = SimpleAESCipher(AES_ENCRYPT_KEY)


def encrypt(plain_text: str) -> str:
    try:
        compressed = compress_to_base64(plain_text)
        if compressed is None:
            return ""
        return _aes.encrypt(compressed)
    except Exception:
        return ""


def decrypt(cipher_text: str) -> str:
    try:
        decrypted = _aes.decrypt(cipher_text.strip())
        result = decompress_from_base64(decrypted)
        return result if result is not None else ""
    except Exception:
        return ""


def extract_cipher_text(raw: str, json_field: str | None = None) -> str:
    text = raw.strip()
    if not text:
        return ""

    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    if not text:
        return ""

    if json_field:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text
        if not isinstance(payload, dict):
            raise ValueError("JSON payload must be an object")
        value = payload.get(json_field)
        if value is None:
            raise ValueError(f"JSON field '{json_field}' not found")
        return str(value).strip()

    if text.startswith("{") or text.startswith("["):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(payload, dict):
            for key in ("result", "data", "content", "message"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return text

    return text


def decrypt_payload(raw: str, json_field: str | None = "result") -> str:
    cipher_text = extract_cipher_text(raw, json_field)
    if not cipher_text:
        return ""
    return decrypt(cipher_text)


def decrypt_json_file(path: str | Path, json_field: str = "result") -> str:
    raw = Path(path).read_text(encoding="utf-8")
    return decrypt_payload(raw, json_field)


def decrypt_batch(
    items: list[tuple[str, str]],
    *,
    json_field: str = "result",
    max_workers: int | None = None,
) -> list[tuple[str, str | None, str | None]]:
    """Decrypt multiple MCP JSON payloads in parallel.

    Returns list of (label, plaintext, error_message).
    """
    if not items:
        return []

    workers = max_workers or min(8, len(items))
    results: dict[str, tuple[str | None, str | None]] = {}

    def _worker(label: str, json_file: str) -> tuple[str, str | None, str | None]:
        try:
            plain_text = decrypt_json_file(json_file, json_field=json_field)
            if not plain_text:
                return label, None, "decrypt failed"
            return label, plain_text, None
        except Exception as exc:
            return label, None, str(exc)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_worker, label, json_file): label
            for label, json_file in items
        }
        for future in as_completed(futures):
            label, plain_text, error = future.result()
            results[label] = (plain_text, error)

    return [(label, *results[label]) for label, _ in items]


def format_batch_output(batch_results: list[tuple[str, str | None, str | None]]) -> str:
    sections: list[str] = []
    for label, plain_text, error in batch_results:
        sections.append(f"=== {label} ===")
        if error:
            sections.append(f"[ERROR] {error}")
        else:
            sections.append(plain_text or "")
        sections.append("")
    return "\n".join(sections).rstrip()
