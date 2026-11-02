"""Chat-completions judges (OpenAI-compatible and Anthropic).

The question set is sent as JSON and the model is asked to answer it in JSON. This is
the portable option: any proxy that implements ``/chat/completions`` or ``/v1/messages``
can act as the judge without knowing anything about pi-rsi.
"""

from __future__ import annotations

from typing import Any

from .. import http
from ..config import JudgeConfig
from .base import Answers, JudgeError, Questions, parse_json_object, prompt, validate

INSTRUCTION = "You are a strict reviewer. Judge only what is asked."


class ChatCompletionsJudge:
    name = "openai"

    def __init__(self, cfg: JudgeConfig) -> None:
        self.cfg = cfg
        self.url = cfg.base_url.rstrip("/") + "/chat/completions"

    def ask(self, questions: Questions) -> Answers:
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": INSTRUCTION},
                {"role": "user", "content": prompt(questions)},
            ],
        }
        try:
            response = http.post_json(
                self.url,
                payload,
                headers=_bearer(self.cfg),
                timeout=self.cfg.timeout_s,
            )
        except http.HttpError as exc:
            raise JudgeError(str(exc)) from exc
        return validate(questions, parse_json_object(_content(response)))


class AnthropicJudge:
    name = "anthropic"

    def __init__(self, cfg: JudgeConfig) -> None:
        self.cfg = cfg
        self.url = cfg.base_url.rstrip("/") + "/v1/messages"

    def ask(self, questions: Questions) -> Answers:
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "max_tokens": 1024,
            "temperature": 0,
            "system": INSTRUCTION,
            "messages": [{"role": "user", "content": prompt(questions)}],
        }
        try:
            response = http.post_json(
                self.url,
                payload,
                headers={
                    **_bearer(self.cfg),
                    "anthropic-version": "2023-06-01",
                },
                timeout=self.cfg.timeout_s,
            )
        except http.HttpError as exc:
            raise JudgeError(str(exc)) from exc
        return validate(questions, parse_json_object(_content(response)))


def _bearer(cfg: JudgeConfig) -> dict[str, str]:
    from ..config import resolve_secret

    try:
        secret = resolve_secret(cfg.api_key)
    except Exception as exc:
        raise JudgeError(f"cannot resolve judge.api_key: {exc}") from exc
    if not secret:
        raise JudgeError("judge.api_key is empty")
    if cfg.provider == "anthropic":
        return {"x-api-key": secret}
    return {"authorization": f"Bearer {secret}"}


def _content(response: dict[str, Any]) -> str:
    """Pull the assistant text out of either response shape."""
    content = response.get("content")
    if isinstance(content, list):  # Anthropic
        parts = [block.get("text", "") for block in content if isinstance(block, dict)]
        return "\n".join(part for part in parts if part)
    if isinstance(content, str):
        return content
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message") or {}
            if isinstance(message, dict):
                text = message.get("content")
                if isinstance(text, str):
                    return text
                if isinstance(text, list):
                    return "\n".join(
                        block.get("text", "") for block in text if isinstance(block, dict)
                    )
    raise JudgeError("response has no assistant text")
