"""Generators that speak a mainstream protocol directly.

``openai``    POST <base_url>/chat/completions
``anthropic`` POST <base_url>/v1/messages

Both ask for a JSON object and let the caller validate it, so a proxy that only
implements the happy path of the protocol is enough.
"""

from __future__ import annotations

from typing import Any

from .. import http
from ..config import GeneratorConfig, resolve_secret
from .base import GeneratorError


class ChatCompletionsGenerator:
    name = "openai"

    def __init__(self, cfg: GeneratorConfig) -> None:
        self.cfg = cfg
        self.url = cfg.base_url.rstrip("/") + "/chat/completions"

    def generate(self, system: str, prompt: str) -> str:
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_output_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        response = _post(
            self.url, payload, {"authorization": f"Bearer {_secret(self.cfg)}"}, self.cfg
        )
        return _extract(response)


class AnthropicGenerator:
    name = "anthropic"

    def __init__(self, cfg: GeneratorConfig) -> None:
        self.cfg = cfg
        self.url = cfg.base_url.rstrip("/") + "/v1/messages"

    def generate(self, system: str, prompt: str) -> str:
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {"x-api-key": _secret(self.cfg), "anthropic-version": "2023-06-01"}
        response = _post(self.url, payload, headers, self.cfg)
        return _extract(response)


def _post(
    url: str, payload: dict[str, Any], headers: dict[str, str], cfg: GeneratorConfig
) -> dict[str, Any]:
    try:
        return http.post_json(url, payload, headers=headers, timeout=cfg.timeout_s)
    except http.HttpError as exc:
        raise GeneratorError(str(exc)) from exc


def _secret(cfg: GeneratorConfig) -> str:
    try:
        secret = resolve_secret(cfg.api_key)
    except Exception as exc:
        raise GeneratorError(f"cannot resolve generator.api_key: {exc}") from exc
    if not secret:
        raise GeneratorError("generator.api_key is empty")
    return secret


def _extract(response: dict[str, Any]) -> str:
    content = response.get("content")
    if isinstance(content, list):
        parts = [block.get("text", "") for block in content if isinstance(block, dict)]
        text = "\n".join(part for part in parts if part)
        if text:
            return text
    if isinstance(content, str) and content:
        return content
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            text = message.get("content")
            if isinstance(text, str):
                return text
            if isinstance(text, list):
                return "\n".join(block.get("text", "") for block in text if isinstance(block, dict))
    raise GeneratorError("response has no assistant text")
