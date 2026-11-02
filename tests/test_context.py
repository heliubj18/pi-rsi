from __future__ import annotations

import json
from pathlib import Path

from conftest import make_session
from pi_rsi import context, facts, skills, store
from pi_rsi.config import Config
from pi_rsi.paths import Paths


def test_context_is_empty_without_state(paths: Paths, cfg: Config, tmp_path: Path) -> None:
    payload = context.build(paths, cfg, cwd=str(tmp_path))
    assert payload["project"] is None
    assert payload["facts"] == ""
    assert payload["skill_paths"] == []
    assert payload["sessions_indexed"] == 0


def test_context_includes_facts_and_skills(paths: Paths, cfg: Config, tmp_path: Path) -> None:
    mapping = tmp_path / "repo"
    mapping.mkdir()
    paths.projects_file.write_text(json.dumps({"proj-a": [str(mapping)]}), encoding="utf-8")
    facts.add(paths, "The staging cluster is read-only.", scope="proj-a")
    facts.add(paths, "Global truth.")
    facts.add(paths, "Other project truth.", scope="proj-b")
    skills.write(paths, name="alpha", description="global skill", body="do it")
    skills.write(paths, name="beta", description="project skill", body="do it", scope="proj-a")

    payload = context.build(paths, cfg, cwd=str(mapping / "sub"))
    assert payload["project"] == "proj-a"
    assert "The staging cluster is read-only." in payload["facts"]
    assert "Global truth." in payload["facts"]
    assert "Other project truth." not in payload["facts"]
    assert {entry["name"] for entry in payload["skills"]} == {"alpha", "beta"}
    assert str(paths.learned_skills) in payload["skill_paths"]
    assert str(paths.skills_for("proj-a")) in payload["skill_paths"]


def test_render_text(paths: Paths, cfg: Config, tmp_path: Path) -> None:
    facts.add(paths, "One truth.")
    payload = context.build(paths, cfg, cwd=str(tmp_path), mapping={})
    text = context.render_text(payload)
    assert "One truth." in text


def test_sessions_indexed_reads_the_database(paths: Paths, cfg: Config, tmp_path: Path) -> None:
    session_file = make_session(tmp_path / "s.jsonl")
    con = store.connect(paths.db)
    try:
        from pi_rsi import episodes

        episodes.index_file(con, session_file, paths=paths, cfg=cfg)
    finally:
        con.close()
    payload = context.build(paths, cfg, cwd=str(tmp_path), mapping={})
    assert payload["sessions_indexed"] == 1


def test_facts_limit_is_bounded(paths: Paths, cfg: Config) -> None:
    for index in range(60):
        facts.add(
            paths, f"Note {index}: subsystem {chr(97 + index % 26)} uses port {7000 + index}."
        )
    payload = context.build(paths, cfg, cwd=None, mapping={})
    assert payload["fact_count"] == 60
    assert len(payload["facts"]) <= cfg.retrieval.max_inject_chars + 200
