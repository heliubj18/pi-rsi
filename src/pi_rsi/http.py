"""Minimal JSON-over-HTTP client.

Only the standard library is used, so the CLI keeps working on a machine that has
Python and nothing else. Tests replace :func:`post_json` through monkeypatching.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

USER_AGENT = "pi-rsi/0.1 (+https://github.com/heliubj18/pi-rsi)"


class HttpError(Exception):
    def __init__(self, message: str, *, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("content-type", "application/json")
    request.add_header("accept", "application/json")
    request.add_header("user-agent", USER_AGENT)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:2000]
        raise HttpError(
            f"POST {url} failed with HTTP {exc.code}: {detail or exc.reason}",
            status=exc.code,
            body=detail,
        ) from exc
    except urllib.error.URLError as exc:
        raise HttpError(f"POST {url} failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise HttpError(f"POST {url} timed out after {timeout:g}s") from exc
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HttpError(f"POST {url} returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HttpError(f"POST {url} returned {type(parsed).__name__}, expected an object")
    return parsed
