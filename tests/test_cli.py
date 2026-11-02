from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_session
from pi_rsi import cli, facts
from pi_rsi.paths import Paths


def run(paths: Paths, *args: str) -> int:
    return cli.main(["--home", str(paths.home), *args])


def test_init_writes_config_and_projects(paths: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    paths.config.unlink(missing_ok=True)
    assert run(paths, "init") == 0
    assert paths.config.exists()
    assert json.loads(paths.projects_file.read_text(encoding="utf-8"))
    output = capsys.readouterr().out
    assert "Next:" in output


def test_init_keeps_existing_files(paths: Paths) -> None:
    run(paths, "init")
    paths.config.write_text("# custom\n", encoding="utf-8")
    run(paths, "init")
    assert paths.config.read_text(encoding="utf-8") == "# custom\n"
    run(paths, "init", "--force")
    assert "generator" in paths.config.read_text(encoding="utf-8")


def test_config_command_masks_keys(paths: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    paths.config.write_text(
        '[generator]\nprovider = "openai"\nbase_url = "https://x.test/v1"\napi_key = "secret-key-0123456789"\n',
        encoding="utf-8",
    )
    assert run(paths, "config") == 0
    out = capsys.readouterr().out
    assert "secret-key-0123456789" not in out
    assert "secr...89" in out


def test_facts_and_forget(paths: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    added = facts.add(paths, "The database is rebuilt nightly.")
    assert run(paths, "facts") == 0
    out = capsys.readouterr().out
    assert added.fact.id in out

    assert run(paths, "forget", added.fact.id) == 0
    capsys.readouterr()
    assert run(paths, "facts", "--all", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["facts"][0]["status"] == "retired"


def test_forget_rejects_bad_id(paths: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(paths, "forget", "nope") == 3
    assert "invalid fact id" in capsys.readouterr().err


def test_index_and_recall(paths: Paths, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    file = make_session(
        tmp_path / "session.jsonl", cwd="/tmp/proj-a", pairs=[("what broke?", "the image tag")]
    )
    assert run(paths, "index", str(file), "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"][0]["turns"] == 1

    assert run(paths, "recall", "image tag", "--all-projects", "--json") == 0
    hits = json.loads(capsys.readouterr().out)["hits"]
    assert hits and hits[0]["turn"] == 0
    assert "section" in hits[0]

    assert run(paths, "stats", "--json") == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["index"]["sessions"] == 1


def test_recall_without_index_is_empty(paths: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(paths, "recall", "anything", "--json") == 0
    assert json.loads(capsys.readouterr().out)["hits"] == []


def test_index_all_requires_a_directory(paths: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(paths, "index", "--all") == 0
    out = capsys.readouterr().out
    assert "0 indexed" in out


def test_project_command(paths: Paths, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    paths.projects_file.write_text(json.dumps({"proj-a": [str(repo)]}), encoding="utf-8")
    assert run(paths, "project", "--cwd", str(repo / "src")) == 0
    assert capsys.readouterr().out.strip() == "proj-a"


def test_skills_command(paths: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    from pi_rsi import skills

    skills.write(paths, name="alpha", description="does a thing", body="step")
    assert run(paths, "skills", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["skills"][0]["name"] == "alpha"


def test_doctor_json(paths: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    run(paths, "doctor", "--json")
    payload = json.loads(capsys.readouterr().out)
    names = {check["name"]: check for check in payload["checks"]}
    assert names["python"]["status"] == "ok"
    assert names["sqlite"]["status"] == "ok"
    assert names["home"]["status"] == "ok"


def test_usage_error_for_missing_arguments(
    paths: Paths, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(paths, "index") == 2
    assert "index needs a file" in capsys.readouterr().err


def test_broken_config_exits_usage(paths: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    paths.config.write_text("[generator]\nprovider = 1\n", encoding="utf-8")
    assert run(paths, "recall", "anything") == 2
    assert "configuration" in capsys.readouterr().err
