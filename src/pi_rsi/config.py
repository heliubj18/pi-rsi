"""User configuration.

Two independent adapters keep the loop from being tied to one vendor:

``[generator]``  writes the skill or fact candidates (``pi``, ``openai``, ``anthropic``).
``[judge]``      answers yes/no and multiple-choice questions about them
                 (``systemone``, ``openai``, ``anthropic``, ``none``).

The defaults select the generator that needs no extra configuration (an extra ``pi``
process, no tools, using any model id pi already knows) and no judge at all. A judge
raises precision and lowers cost: it answers a batch of cheap questions first and
decides whether the expensive generator call is worth making.

Secrets are never stored inline when avoidable. ``api_key`` accepts ``env:NAME``,
``file:PATH`` (first line, trimmed) or a literal string.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .paths import Paths, default_sessions_dir

GENERATOR_PROVIDERS = ("pi", "openai", "anthropic")
JUDGE_PROVIDERS = ("none", "systemone", "openai", "anthropic")

DEFAULT_THRESHOLDS: dict[str, float] = {
    # Gate: both below threshold means no generator call at all.
    "gate_skill": 0.5,
    "gate_fact": 0.5,
    # Fact lifecycle.
    "fact_contradicted": 0.8,
    "fact_durable": 0.5,
    "fact_supersedes": 0.6,
    # Scope.
    "scope_global": 0.8,
    # Feedback.
    "skill_blamed": 0.5,
    # Recall.
    "recall_relevant": 0.5,
    "recall_inject": 0.8,
    "recall_recent": 0.6,
}

ENV_PREFIX = "RSI_"


class ConfigError(Exception):
    """Configuration is absent or malformed. Never downgraded to a warning."""


@dataclass(slots=True)
class GeneratorConfig:
    provider: str = "pi"
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    timeout_s: float = 180.0
    max_output_tokens: int = 4096
    temperature: float = 0.2


@dataclass(slots=True)
class JudgeConfig:
    provider: str = "none"
    base_url: str = ""
    path: str = "/v3/decisions"
    model: str = ""
    api_key: str = ""
    timeout_s: float = 30.0
    thresholds: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))

    def threshold(self, name: str) -> float:
        return self.thresholds.get(name, DEFAULT_THRESHOLDS[name])


@dataclass(slots=True)
class LearningConfig:
    enabled: bool = True
    max_skills_total: int = 30
    max_skills_per_day: int = 3
    max_facts_per_session: int = 3
    max_facts_per_day: int = 10
    max_facts_per_scope: int = 40
    digest_chars: int = 24000
    min_tool_calls: int = 3
    min_user_turns: int = 2


@dataclass(slots=True)
class RetrievalConfig:
    candidates: int = 20
    recent: int = 8
    max_turn_chars: int = 6000
    max_inject_chars: int = 4000


@dataclass(slots=True)
class Config:
    generator: GeneratorConfig = field(default_factory=GeneratorConfig)
    judge: JudgeConfig = field(default_factory=JudgeConfig)
    learning: LearningConfig = field(default_factory=LearningConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    sessions_dir: Path = field(default_factory=default_sessions_dir)
    source: Path | None = None


def _bool(value: Any, *, name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ConfigError(f"{name} must be a boolean")


def _number(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} must be a number")
    return float(value)


def _integer(value: Any, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer")
    return value


def _string(value: Any, *, name: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be a string")
    return value


def _table(raw: dict[str, Any], name: str, allowed: set[str]) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    unknown = set(value) - allowed
    if unknown:
        raise ConfigError(f"[{name}] has unknown keys: {', '.join(sorted(unknown))}")
    return value


def _parse_generator(raw: dict[str, Any]) -> GeneratorConfig:
    table = _table(
        raw,
        "generator",
        {
            "provider",
            "model",
            "base_url",
            "api_key",
            "timeout_s",
            "max_output_tokens",
            "temperature",
        },
    )
    cfg = GeneratorConfig()
    if "provider" in table:
        cfg.provider = _string(table["provider"], name="generator.provider")
    if cfg.provider not in GENERATOR_PROVIDERS:
        raise ConfigError(
            f"generator.provider must be one of {', '.join(GENERATOR_PROVIDERS)}; got {cfg.provider!r}"
        )
    for name in ("model", "base_url", "api_key"):
        if name in table:
            setattr(cfg, name, _string(table[name], name=f"generator.{name}"))
    if "timeout_s" in table:
        cfg.timeout_s = _number(table["timeout_s"], name="generator.timeout_s")
    if "max_output_tokens" in table:
        cfg.max_output_tokens = _integer(
            table["max_output_tokens"], name="generator.max_output_tokens"
        )
    if "temperature" in table:
        cfg.temperature = _number(table["temperature"], name="generator.temperature")
    if cfg.provider in ("openai", "anthropic") and not cfg.base_url:
        raise ConfigError(
            f"generator.base_url is required when generator.provider = {cfg.provider!r}"
        )
    return cfg


def _parse_judge(raw: dict[str, Any]) -> JudgeConfig:
    table = _table(
        raw,
        "judge",
        {"provider", "base_url", "path", "model", "api_key", "timeout_s", "thresholds"},
    )
    cfg = JudgeConfig()
    if "provider" in table:
        cfg.provider = _string(table["provider"], name="judge.provider")
    if cfg.provider not in JUDGE_PROVIDERS:
        raise ConfigError(
            f"judge.provider must be one of {', '.join(JUDGE_PROVIDERS)}; got {cfg.provider!r}"
        )
    for name in ("base_url", "path", "model", "api_key"):
        if name in table:
            setattr(cfg, name, _string(table[name], name=f"judge.{name}"))
    if "timeout_s" in table:
        cfg.timeout_s = _number(table["timeout_s"], name="judge.timeout_s")
    thresholds = table.get("thresholds", {})
    if not isinstance(thresholds, dict):
        raise ConfigError("[judge.thresholds] must be a table")
    unknown = set(thresholds) - set(DEFAULT_THRESHOLDS)
    if unknown:
        raise ConfigError(f"[judge.thresholds] has unknown keys: {', '.join(sorted(unknown))}")
    cfg.thresholds = dict(DEFAULT_THRESHOLDS)
    for key, value in thresholds.items():
        score = _number(value, name=f"judge.thresholds.{key}")
        if not 0.0 <= score <= 1.0:
            raise ConfigError(f"judge.thresholds.{key} must be within [0, 1]")
        cfg.thresholds[key] = score
    if cfg.provider == "systemone" and not cfg.base_url:
        raise ConfigError("judge.base_url is required when judge.provider = 'systemone'")
    if cfg.provider == "openai" and not cfg.base_url:
        raise ConfigError("judge.base_url is required when judge.provider = 'openai'")
    if cfg.provider == "anthropic" and not cfg.base_url:
        raise ConfigError("judge.base_url is required when judge.provider = 'anthropic'")
    return cfg


def _parse_learning(raw: dict[str, Any]) -> LearningConfig:
    cfg = LearningConfig()
    table = _table(raw, "learning", {f.name for f in cfg.__dataclass_fields__.values()})
    for key, value in table.items():
        if isinstance(getattr(cfg, key), bool):
            setattr(cfg, key, _bool(value, name=f"learning.{key}"))
        else:
            setattr(cfg, key, _integer(value, name=f"learning.{key}"))
    return cfg


def _parse_retrieval(raw: dict[str, Any]) -> RetrievalConfig:
    cfg = RetrievalConfig()
    table = _table(raw, "retrieval", {f.name for f in cfg.__dataclass_fields__.values()})
    for key, value in table.items():
        setattr(cfg, key, _integer(value, name=f"retrieval.{key}"))
    return cfg


def load(paths: Paths | None = None) -> Config:
    """Load configuration, applying environment overrides.

    A missing file is not an error: it means the defaults (pi generator, no judge).
    ``rsi doctor`` reports the difference so the omission is visible.
    """
    paths = paths or Paths.resolve()
    raw: dict[str, Any] = {}
    source: Path | None = None
    if paths.config.exists():
        source = paths.config
        try:
            raw = tomllib.loads(paths.config.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{paths.config}: {exc}") from exc
    unknown = set(raw) - {"generator", "judge", "learning", "retrieval", "sessions"}
    if unknown:
        raise ConfigError(f"{paths.config} has unknown sections: {', '.join(sorted(unknown))}")
    cfg = Config(
        generator=_parse_generator(raw),
        judge=_parse_judge(raw),
        learning=_parse_learning(raw),
        retrieval=_parse_retrieval(raw),
        source=source,
    )
    sessions = _table(raw, "sessions", {"dir"})
    directory = _string(sessions.get("dir", ""), name="sessions.dir").strip()
    if directory:
        cfg.sessions_dir = Path(directory).expanduser()
    # Precedence: RSI_SESSIONS_DIR, then [sessions] dir, then where pi itself keeps
    # sessions (resolved in paths.default_sessions_dir).
    cfg.sessions_dir = Path(
        os.environ.get(f"{ENV_PREFIX}SESSIONS_DIR") or cfg.sessions_dir
    ).expanduser()
    return _apply_env(cfg)


def _apply_env(cfg: Config) -> Config:
    generator = cfg.generator
    judge = cfg.judge
    learning = cfg.learning
    if provider := os.environ.get(f"{ENV_PREFIX}GENERATOR_PROVIDER"):
        if provider not in GENERATOR_PROVIDERS:
            raise ConfigError(
                f"RSI_GENERATOR_PROVIDER must be one of {', '.join(GENERATOR_PROVIDERS)}"
            )
        generator = replace(generator, provider=provider)
    for env, attr in (
        ("GENERATOR_MODEL", "model"),
        ("GENERATOR_BASE_URL", "base_url"),
        ("GENERATOR_API_KEY", "api_key"),
    ):
        if value := os.environ.get(f"{ENV_PREFIX}{env}"):
            generator = replace(generator, **{attr: value})
    if provider := os.environ.get(f"{ENV_PREFIX}JUDGE_PROVIDER"):
        if provider not in JUDGE_PROVIDERS:
            raise ConfigError(f"RSI_JUDGE_PROVIDER must be one of {', '.join(JUDGE_PROVIDERS)}")
        judge = replace(judge, provider=provider)
    for env, attr in (
        ("JUDGE_BASE_URL", "base_url"),
        ("JUDGE_MODEL", "model"),
        ("JUDGE_API_KEY", "api_key"),
    ):
        if value := os.environ.get(f"{ENV_PREFIX}{env}"):
            judge = replace(judge, **{attr: value})
    if os.environ.get(f"{ENV_PREFIX}LEARN", "").strip() in ("0", "false", "no"):
        learning = replace(learning, enabled=False)
    return Config(
        generator=generator,
        judge=judge,
        learning=learning,
        retrieval=cfg.retrieval,
        sessions_dir=cfg.sessions_dir,
        source=cfg.source,
    )


def resolve_secret(spec: str) -> str:
    """Resolve ``env:NAME``, ``file:PATH`` or a literal."""
    if not spec:
        return ""
    if spec.startswith("env:"):
        name = spec[4:]
        value = os.environ.get(name, "")
        if not value:
            raise ConfigError(f"environment variable {name} is empty or unset")
        return value
    if spec.startswith("file:"):
        path = Path(spec[5:]).expanduser()
        try:
            return path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        except (OSError, IndexError) as exc:
            raise ConfigError(f"cannot read {path}: {exc}") from exc
    return spec


def mask(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-2:]}"


def render(cfg: Config) -> str:
    """Human-readable effective configuration, with every secret masked."""
    generator_key = mask(_safe_secret(cfg.generator.api_key))
    judge_key = mask(_safe_secret(cfg.judge.api_key))
    lines = [
        "# effective configuration",
        f"source              {cfg.source or '(defaults: no config.toml)'}",
        f"sessions_dir        {cfg.sessions_dir}",
        "",
        "[generator]",
        f"provider            {cfg.generator.provider}",
        f"model               {cfg.generator.model or '(pi default model)'}",
        f"base_url            {cfg.generator.base_url or '-'}",
        f"api_key             {generator_key or '-'}",
        "",
        "[judge]",
        f"provider            {cfg.judge.provider}",
        f"base_url            {cfg.judge.base_url or '-'}",
        f"model               {cfg.judge.model or '-'}",
        f"api_key             {judge_key or '-'}",
        "",
        "[learning]",
        f"enabled             {cfg.learning.enabled}",
        f"skills total/day    {cfg.learning.max_skills_total}/{cfg.learning.max_skills_per_day}",
        f"facts session/day   {cfg.learning.max_facts_per_session}/{cfg.learning.max_facts_per_day}",
        "",
        "[judge.thresholds]",
    ]
    for key, value in cfg.judge.thresholds.items():
        lines.append(f"{key:<20}{value}")
    return "\n".join(lines)


def _safe_secret(spec: str) -> str:
    try:
        return resolve_secret(spec)
    except ConfigError:
        return "<unresolved>"
