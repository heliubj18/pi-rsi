"""Judge factory."""

from __future__ import annotations

from ..config import JudgeConfig
from .base import (
    Answers,
    Judge,
    JudgeError,
    Questions,
    choice,
    noul,
    option,
    parse_json_object,
    probability,
    validate,
)

__all__ = [
    "Answers",
    "Judge",
    "JudgeError",
    "Questions",
    "build",
    "choice",
    "noul",
    "option",
    "parse_json_object",
    "probability",
    "validate",
]


def build(cfg: JudgeConfig) -> Judge | None:
    """Return a judge, or ``None`` when judging is disabled (``provider = "none"``)."""
    if cfg.provider == "none":
        return None
    if cfg.provider == "systemone":
        from .systemone import SystemOneJudge

        return SystemOneJudge(cfg)
    from .chat import AnthropicJudge, ChatCompletionsJudge

    return AnthropicJudge(cfg) if cfg.provider == "anthropic" else ChatCompletionsJudge(cfg)
