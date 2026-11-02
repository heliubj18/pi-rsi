"""Redaction and secret detection.

Two jobs, one implementation:

* Every turn is redacted before it is indexed, so ``recall`` cannot move a credential
  from one project into another context.
* Every generated skill or fact is scanned and rejected whole if it matches a secret
  pattern: a skill is read by the model, so a leak there is worse than a leak in a log.

Patterns are deliberately conservative (long, structured tokens). Extra project-specific
rules can be added in ``$RSI_HOME/guard.json``::

    {"redact": [{"id": "internal-host", "pattern": "\\\\b10\\\\.\\\\d+\\\\.\\\\d+\\\\.\\\\d+\\\\b",
                 "flags": "", "replace": "[HOST]"}]}
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError

REDACTED = "[REDACTED]"


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern[str]
    replace: str


def _rule(id: str, pattern: str, replace: str, flags: int = 0) -> Rule:
    return Rule(id=id, pattern=re.compile(pattern, flags), replace=replace)


BUILTIN: tuple[Rule, ...] = (
    _rule(
        "private-key",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        "[REDACTED PRIVATE KEY]",
        re.S,
    ),
    _rule("bearer", r"\bBearer\s+(?![$<{])[A-Za-z0-9._~+/=-]{16,}", "Bearer " + REDACTED),
    _rule(
        "api-key-header",
        r"\b(?:x-api-key|api-key|authorization)\s*:\s*['\"]?(?![$<{])[A-Za-z0-9._~+/=-]{16,}",
        "[REDACTED HEADER]",
        re.I,
    ),
    _rule("sk-key", r"\bsk-[A-Za-z0-9_-]{20,}", REDACTED),
    _rule(
        "github-token", r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})", REDACTED
    ),
    _rule("aws-access-key", r"\bAKIA[0-9A-Z]{16}\b", REDACTED),
    _rule("google-api-key", r"\bAIza[0-9A-Za-z_-]{30,}", REDACTED),
    _rule("slack-token", r"\bxox[baprs]-[A-Za-z0-9-]{10,}", REDACTED),
    _rule(
        "assignment",
        r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret[_-]?key|password)\b\s*[:=]\s*['\"]?(?![$<{])([A-Za-z0-9._~+/=-]{16,})",
        "api_key=" + REDACTED,
    ),
)


def load_rules(path: Path) -> tuple[Rule, ...]:
    """Built-in rules plus any configured in ``guard.json``."""
    if not path.exists():
        return BUILTIN
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    entries = raw.get("redact", [])
    if not isinstance(entries, list):
        raise ConfigError(f"{path}: 'redact' must be a list")
    extra: list[Rule] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ConfigError(f"{path}: redact[{index}] must be an object")
        try:
            flags = 0
            for letter in str(entry.get("flags", "")):
                flags |= {"i": re.I, "m": re.M, "s": re.S}.get(letter, 0)
            extra.append(
                _rule(
                    str(entry.get("id") or f"extra-{index}"),
                    str(entry["pattern"]),
                    str(entry.get("replace", REDACTED)),
                    flags,
                )
            )
        except KeyError as exc:
            raise ConfigError(f"{path}: redact[{index}] is missing {exc}") from exc
        except re.error as exc:
            raise ConfigError(f"{path}: redact[{index}] has an invalid pattern: {exc}") from exc
    return BUILTIN + tuple(extra)


def redact(text: str, rules: tuple[Rule, ...] = BUILTIN) -> str:
    for rule in rules:
        text = rule.pattern.sub(rule.replace, text)
    return text


def find_secret(text: str, rules: tuple[Rule, ...] = BUILTIN) -> str | None:
    """Return the id of the first matching rule, or ``None``."""
    for rule in rules:
        if rule.pattern.search(text):
            return rule.id
    return None
