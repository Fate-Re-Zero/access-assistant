"""GZIP + Base64 compression utilities."""

from __future__ import annotations

import base64
import gzip


def compress_to_base64(data: str | None) -> str | None:
    if not data:
        return data
    compressed = gzip.compress(data.encode("utf-8"))
    return base64.b64encode(compressed).decode("ascii")


def decompress_from_base64(compressed_base64: str | None) -> str | None:
    if not compressed_base64:
        return compressed_base64
    compressed_bytes = base64.b64decode(compressed_base64)
    return gzip.decompress(compressed_bytes).decode("utf-8")
