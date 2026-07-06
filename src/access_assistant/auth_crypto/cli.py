"""CLI for auth MCP result decrypt."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .decrypt import (
    decrypt,
    decrypt_batch,
    encrypt,
    extract_cipher_text,
    format_batch_output,
)


def _read_cipher_from_args(args: argparse.Namespace) -> str:
    if args.json_file:
        raw = Path(args.json_file).read_text(encoding="utf-8")
        return extract_cipher_text(raw, args.json_field)
    if args.file:
        raw = Path(args.file).read_text(encoding="utf-8")
        return extract_cipher_text(raw)
    if args.json is not None:
        return extract_cipher_text(args.json, args.json_field)
    if args.cipher is not None:
        return extract_cipher_text(args.cipher)
    if not sys.stdin.isatty():
        return extract_cipher_text(sys.stdin.read())
    raise ValueError("must provide --cipher, --file, --json, --json-file, or stdin")


def main() -> int:
    parser = argparse.ArgumentParser(description="Auth MCP result encrypt/decrypt helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    decrypt_parser = subparsers.add_parser("decrypt", help="Decrypt auth MCP tool result")
    decrypt_parser.add_argument("--cipher", help="Plain cipher text (Base64)")
    decrypt_parser.add_argument("--file", help="Read cipher text or JSON from file")
    decrypt_parser.add_argument("--json", help="JSON string containing encrypted result")
    decrypt_parser.add_argument(
        "--json-file",
        help="Read JSON payload from file (recommended on Windows for long MCP responses)",
    )
    decrypt_parser.add_argument(
        "--json-field",
        default="result",
        help="JSON field name that stores cipher text (only used with --json / --json-file)",
    )

    batch_parser = subparsers.add_parser(
        "decrypt-batch",
        help="Decrypt multiple auth MCP JSON files in parallel",
    )
    batch_parser.add_argument(
        "--json-file",
        action="append",
        required=True,
        help="MCP JSON response file; repeat for multiple inputs",
    )
    batch_parser.add_argument(
        "--label",
        action="append",
        help="Optional label for each --json-file (same order); default uses file stem",
    )
    batch_parser.add_argument(
        "--json-field",
        default="result",
        help="JSON field name that stores cipher text",
    )

    encrypt_parser = subparsers.add_parser("encrypt", help="Encrypt plain text")
    encrypt_parser.add_argument("--plain", required=True, help="Plain text to encrypt")

    args = parser.parse_args()

    if args.command == "encrypt":
        print(encrypt(args.plain))
        return 0

    if args.command == "decrypt-batch":
        labels = list(args.label or [])
        json_files = list(args.json_file)
        if labels and len(labels) != len(json_files):
            print("[ERROR] --label count must match --json-file count", file=sys.stderr)
            return 1

        items: list[tuple[str, str]] = []
        for index, json_file in enumerate(json_files):
            label = labels[index] if labels else Path(json_file).stem
            items.append((label, json_file))

        batch_results = decrypt_batch(items, json_field=args.json_field)
        print(format_batch_output(batch_results))
        if any(error for _, _, error in batch_results):
            return 1
        return 0

    cipher_text = _read_cipher_from_args(args)
    if not cipher_text:
        print("[ERROR] cipher text is empty", file=sys.stderr)
        return 1

    plain_text = decrypt(cipher_text)
    if not plain_text:
        print("[ERROR] decrypt failed", file=sys.stderr)
        return 1

    print(plain_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
