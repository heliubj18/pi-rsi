"""Generator factory."""

from __future__ import annotations

from ..config import GeneratorConfig
from .base import Generator, GeneratorError, parse_output

__all__ = ["Generator", "GeneratorError", "build", "parse_output"]


def build(cfg: GeneratorConfig) -> Generator:
    if cfg.provider == "pi":
        from .pi import PiGenerator

        return PiGenerator(cfg)
    from .chat import AnthropicGenerator, ChatCompletionsGenerator

    return AnthropicGenerator(cfg) if cfg.provider == "anthropic" else ChatCompletionsGenerator(cfg)
