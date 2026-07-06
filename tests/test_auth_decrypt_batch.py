from __future__ import annotations

import json
from pathlib import Path

from access_assistant.auth_crypto.decrypt import decrypt_batch, encrypt, format_batch_output


def test_decrypt_batch_parallel(tmp_path: Path):
    plain_a = "账号状态正常"
    plain_b = "最近登录失败 3 次"
    file_a = tmp_path / "mcp_account_info.json"
    file_b = tmp_path / "mcp_login_behavior.json"
    file_a.write_text(json.dumps({"result": encrypt(plain_a)}), encoding="utf-8")
    file_b.write_text(json.dumps({"result": encrypt(plain_b)}), encoding="utf-8")

    results = decrypt_batch(
        [
            ("account_info", str(file_a)),
            ("login_behavior", str(file_b)),
        ]
    )
    output = format_batch_output(results)

    assert results[0][1] == plain_a
    assert results[1][1] == plain_b
    assert "=== account_info ===" in output
    assert "=== login_behavior ===" in output
    assert plain_a in output
    assert plain_b in output
