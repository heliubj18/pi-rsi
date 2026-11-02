from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from pi_rsi import config
from pi_rsi.paths import Paths


def test_defaults_without_config_file(paths: Paths) -> None:
    cfg = config.load(paths)
    assert cfg.generator.provider == "pi"
    assert cfg.generator.model == ""
    assert cfg.judge.provider == "none"
    assert cfg.learning.enabled is True
    assert cfg.source is None
    assert cfg.sessions_dir.name == "sessions"


def test_config_file_overrides(paths: Paths) -> None:
    paths.config.write_text(
        """
[generator]
provider = "openai"
base_url = "https://api.example.test/v1"
model = "some-model"
api_key = "env:TEST_RSI_KEY"

[judge]
provider = "openai"
base_url = "https://api.example.test/v1"
model = "small-model"

[judge.thresholds]
gate_skill = 0.7

[learning]
max_facts_per_day = 2

[sessions]
dir = "~/somewhere/sessions"
""",
        encoding="utf-8",
    )
    cfg = config.load(paths)
    assert cfg.generator.provider == "openai"
    assert cfg.generator.model == "some-model"
    assert cfg.judge.threshold("gate_skill") == 0.7
    assert cfg.judge.threshold("gate_fact") == 0.5
    assert cfg.learning.max_facts_per_day == 2
    assert cfg.sessions_dir == Path("~/somewhere/sessions").expanduser()
    assert cfg.source == paths.config


def test_unknown_section_is_a_failure(paths: Paths) -> None:
    paths.config.write_text("[nonsense]\nx = 1\n", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="unknown sections"):
        config.load(paths)


def test_unknown_key_is_a_failure(paths: Paths) -> None:
    paths.config.write_text('[generator]\nprovider = "pi"\nmodle = "typo"\n', encoding="utf-8")
    with pytest.raises(config.ConfigError, match="unknown keys"):
        config.load(paths)


def test_unknown_provider_is_a_failure(paths: Paths) -> None:
    paths.config.write_text('[generator]\nprovider = "gemini"\n', encoding="utf-8")
    with pytest.raises(config.ConfigError, match=r"generator\.provider"):
        config.load(paths)


def test_threshold_out_of_range_is_a_failure(paths: Paths) -> None:
    paths.config.write_text("[judge.thresholds]\ngate_fact = 1.5\n", encoding="utf-8")
    with pytest.raises(config.ConfigError, match=r"within \[0, 1\]"):
        config.load(paths)


def test_remote_provider_requires_base_url(paths: Paths) -> None:
    paths.config.write_text('[generator]\nprovider = "anthropic"\n', encoding="utf-8")
    with pytest.raises(config.ConfigError, match="base_url is required"):
        config.load(paths)


def test_broken_toml_is_a_failure(paths: Paths) -> None:
    paths.config.write_text("[generator\n", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="table declaration"):
        config.load(paths)


def test_environment_overrides(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RSI_GENERATOR_MODEL", "env-model")
    monkeypatch.setenv("RSI_JUDGE_PROVIDER", "systemone")
    monkeypatch.setenv("RSI_LEARN", "0")
    cfg = config.load(paths)
    assert cfg.generator.model == "env-model"
    assert cfg.judge.provider == "systemone"
    assert cfg.learning.enabled is False


def test_secret_resolution(paths: Paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TEST_RSI_SECRET", "from-env")
    assert config.resolve_secret("env:TEST_RSI_SECRET") == "from-env"
    assert config.resolve_secret("") == ""
    assert config.resolve_secret("literal-key") == "literal-key"
    keyfile = tmp_path / "key"
    keyfile.write_text("  from-file  \nignored\n", encoding="utf-8")
    assert config.resolve_secret(f"file:{keyfile}") == "from-file"
    with pytest.raises(config.ConfigError, match="empty or unset"):
        config.resolve_secret("env:TEST_RSI_MISSING")


def test_render_masks_secrets(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_RSI_SECRET", "sk-abcdefghijklmnopqrstuvwxyz")
    paths.config.write_text(
        '[generator]\nprovider = "openai"\nbase_url = "https://x.test/v1"\napi_key = "env:TEST_RSI_SECRET"\n',
        encoding="utf-8",
    )
    rendered = config.render(config.load(paths))
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in rendered
    assert "sk-a...yz" in rendered


def test_init_template_parses(paths: Paths) -> None:
    from pi_rsi.cli import CONFIG_TEMPLATE, PROJECTS_TEMPLATE

    assert tomllib.loads(CONFIG_TEMPLATE)["generator"]["provider"] == "pi"
    assert json.loads(PROJECTS_TEMPLATE)["my-project"]


def test_example_config_matches_the_init_template() -> None:
    """The shipped example and what `rsi init` writes must not drift apart."""
    from pi_rsi.cli import CONFIG_TEMPLATE

    example = Path(__file__).resolve().parents[1] / "config" / "config.example.toml"
    assert example.read_text(encoding="utf-8") == CONFIG_TEMPLATE
