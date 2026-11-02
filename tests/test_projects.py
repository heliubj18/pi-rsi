from __future__ import annotations

import json
from pathlib import Path

import pytest

from pi_rsi import projects
from pi_rsi.config import ConfigError
from pi_rsi.paths import Paths


def test_longest_prefix_wins(tmp_path: Path) -> None:
    mapping = {
        "outer": [tmp_path / "src"],
        "inner": [tmp_path / "src" / "api"],
    }
    assert projects.resolve(tmp_path / "src" / "api" / "pkg", mapping=mapping) == "inner"
    assert projects.resolve(tmp_path / "src" / "web", mapping=mapping) == "outer"
    assert projects.resolve(tmp_path / "elsewhere", mapping=mapping) is None


def test_missing_cwd_is_none() -> None:
    assert projects.resolve(None) is None
    assert projects.resolve("") is None


def test_git_toplevel_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    mapping = {"repo": [repo]}
    monkeypatch.setattr(projects, "_git_toplevel", lambda path: repo)
    assert projects.resolve(tmp_path / "repo" / "nested", mapping=mapping) == "repo"


def test_load_expands_home(paths: Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    paths.projects_file.write_text(json.dumps({"proj": ["~/src/proj"]}), encoding="utf-8")
    mapping = projects.load(paths)
    assert mapping["proj"] == [tmp_path / "src" / "proj"]


def test_load_rejects_bad_shapes(paths: Paths) -> None:
    paths.projects_file.write_text(json.dumps(["proj"]), encoding="utf-8")
    with pytest.raises(ConfigError, match="expected an object"):
        projects.load(paths)
    paths.projects_file.write_text(json.dumps({"proj": [1]}), encoding="utf-8")
    with pytest.raises(ConfigError, match="list of directories"):
        projects.load(paths)
    paths.projects_file.write_text(json.dumps({"bad/name": []}), encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid project name"):
        projects.load(paths)


def test_missing_file_is_empty(paths: Paths) -> None:
    assert projects.load(paths) == {}
