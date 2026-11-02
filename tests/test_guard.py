from __future__ import annotations

import json
from pathlib import Path

import pytest

from pi_rsi import guard
from pi_rsi.config import ConfigError


def test_redacts_common_credentials() -> None:
    text = (
        "curl -H 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345' "
        "-H 'x-api-key: 1234567890abcdefghij' "
        "and sk-abcdefghijklmnopqrstuvwx and ghp_abcdefghijklmnopqrstuvwxyz0123 "
        "and AKIAABCDEFGHIJKLMNOP"
    )
    out = guard.redact(text)
    assert "abcdefghijklmnopqrstuvwxyz012345" not in out
    assert "1234567890abcdefghij" not in out
    assert "sk-abcdefghijklmnopqrstuvwx" not in out
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123" not in out
    assert "AKIAABCDEFGHIJKLMNOP" not in out
    assert out.count(guard.REDACTED) >= 4


def test_redacts_private_key_blocks() -> None:
    text = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAAB3NzaC1yc2E\n-----END OPENSSH PRIVATE KEY-----"
    assert guard.redact(text) == "[REDACTED PRIVATE KEY]"


def test_leaves_ordinary_text_alone() -> None:
    text = "The token budget is 4096 and the key is stored in a host file."
    assert guard.redact(text) == text
    assert guard.find_secret(text) is None


def test_find_secret_names_the_rule() -> None:
    assert guard.find_secret("token sk-abcdefghijklmnopqrstuvwx") == "sk-key"
    assert guard.find_secret("api_key = 0123456789abcdef0123") == "assignment"


def test_extra_rules_from_file(tmp_path: Path) -> None:
    rules_file = tmp_path / "guard.json"
    rules_file.write_text(
        json.dumps(
            {
                "redact": [
                    {"id": "internal-host", "pattern": r"\b10\.0\.\d+\.\d+\b", "replace": "[HOST]"}
                ]
            }
        ),
        encoding="utf-8",
    )
    rules = guard.load_rules(rules_file)
    assert guard.redact("connect to 10.0.7.4 now", rules) == "connect to [HOST] now"
    assert len(rules) == len(guard.BUILTIN) + 1


def test_missing_rules_file_is_builtin(tmp_path: Path) -> None:
    assert guard.load_rules(tmp_path / "absent.json") == guard.BUILTIN


def test_invalid_rules_file_is_a_failure(tmp_path: Path) -> None:
    bad = tmp_path / "guard.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        guard.load_rules(bad)
    bad.write_text(json.dumps({"redact": [{"pattern": "("}]}), encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid pattern"):
        guard.load_rules(bad)
