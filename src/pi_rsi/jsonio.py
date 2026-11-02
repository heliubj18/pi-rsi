"""Tolerant JSON extraction.

Models wrap JSON in prose or fences. Both the learner and the judges accept the first
complete object and nothing else, so a truncated or double answer fails loudly.
"""

from __future__ import annotations

import json
from typing import Any


class JsonError(ValueError):
    pass


def parse_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    start = text.find("{")
    if start < 0:
        raise JsonError("response contains no JSON object")
    try:
        parsed, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise JsonError(f"response is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise JsonError(f"response is a {type(parsed).__name__}, expected an object")
    return parsed
