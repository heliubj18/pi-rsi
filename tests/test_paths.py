"""Defaults follow pi's own locations, so a relocated pi home carries rsi with it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pi_rsi import config
from pi_rsi.paths import Paths, default_home, default_sessions_dir


def test_home_defaults_to_a_sibling_of_pis_agent_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "nix" / "agent"))
    assert default_home() == tmp_path / "nix" / "rsi"
    assert Paths.resolve().home == (tmp_path / "nix" / "rsi").resolve()


def test_home_default_is_unchanged_for_the_standard_location(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PI_CODING_AGENT_DIR", raising=False)
    assert default_home() == tmp_path / ".pi" / "rsi"


def test_sessions_dir_follows_pis_environment_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PI_CODING_AGENT_SESSION_DIR", str(tmp_path / "elsewhere"))
    assert default_sessions_dir() == tmp_path / "elsewhere"


def test_sessions_dir_follows_pis_settings_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "settings.json").write_text(
        json.dumps({"sessionDir": str(tmp_path / "from-settings")}), encoding="utf-8"
    )
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
    assert default_sessions_dir() == tmp_path / "from-settings"


def test_sessions_dir_ignores_a_relative_setting_and_broken_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent = tmp_path / "agent"
    agent.mkdir()
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
    (agent / "settings.json").write_text('{"sessionDir": "sessions"}', encoding="utf-8")
    assert default_sessions_dir() == agent / "sessions"
    (agent / "settings.json").write_text("{not json", encoding="utf-8")
    assert default_sessions_dir() == agent / "sessions"


def test_config_dir_beats_pis_environment_but_rsi_env_beats_both(
    paths: Paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PI_CODING_AGENT_SESSION_DIR", str(tmp_path / "pi-env"))
    paths.config.write_text("[sessions]\ndir = '~/from-config'\n", encoding="utf-8")
    assert config.load(paths).sessions_dir == Path("~/from-config").expanduser()

    monkeypatch.setenv("RSI_SESSIONS_DIR", str(tmp_path / "rsi-env"))
    assert config.load(paths).sessions_dir == tmp_path / "rsi-env"
