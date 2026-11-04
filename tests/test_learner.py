from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from conftest import FakeGenerator, FakeJudge, make_session
from pi_rsi import facts, learner, skills, store, util
from pi_rsi.config import Config
from pi_rsi.paths import Paths


def map_project(paths: Paths, name: str = "proj-a", directory: str = "/tmp/proj-a") -> None:
    paths.projects_file.write_text(json.dumps({name: [directory]}), encoding="utf-8")


def session_file(tmp_path: Path, **kwargs) -> Path:
    return make_session(tmp_path / "session.jsonl", **kwargs)


def run(paths: Paths, cfg: Config, file: Path, **kwargs) -> dict:
    return learner.learn(paths, file, cfg, **kwargs)


def test_a_broken_index_does_not_cost_the_session(
    paths: Paths, cfg: Config, payload: dict, tmp_path: Path, monkeypatch
) -> None:
    """A missing FTS5/trigram tokenizer is an environment problem, not a reason to skip."""

    def broken(path, *, readonly=False):
        raise sqlite3.OperationalError("no such tokenizer: trigram")

    monkeypatch.setattr(store, "open_db", broken)
    map_project(paths)
    summary = run(
        paths,
        cfg,
        session_file(tmp_path),
        generator=FakeGenerator(payload),
        judge=FakeJudge({"scope": 0.0, "global": 0.0}),
    )
    assert summary["status"] == "learned"
    assert "trigram" in summary["index_error"]
    assert facts.read_file(paths.facts_for("proj-a"), "proj-a")


def test_happy_path_writes_scoped_skill_and_fact(
    paths: Paths, cfg: Config, payload: dict, tmp_path: Path
) -> None:
    map_project(paths)
    judge = FakeJudge({"scope": 0.0, "global": 0.0})
    generator = FakeGenerator(payload)
    summary = run(paths, cfg, session_file(tmp_path), generator=generator, judge=judge)

    assert summary["status"] == "learned"
    assert summary["project"] == "proj-a"
    assert len(generator.calls) == 1
    skill_file = paths.project_dir("proj-a") / "skills" / "stale-image-tag-triage" / "SKILL.md"
    assert skill_file.exists()
    stored = facts.read_file(paths.facts_for("proj-a"), "proj-a")
    assert stored[0].text.startswith("Deploys use the image tag")
    assert stored[0].scope == "proj-a"


def test_global_scope_needs_a_confident_judge(
    paths: Paths, cfg: Config, payload: dict, tmp_path: Path
) -> None:
    map_project(paths)
    judge = FakeJudge({"scope": 0.95, "global": 0.95})
    summary = run(paths, cfg, session_file(tmp_path), generator=FakeGenerator(payload), judge=judge)
    assert summary["status"] == "learned"
    assert (paths.learned_skills / "stale-image-tag-triage" / "SKILL.md").exists()
    assert facts.read_file(paths.facts_file, "global")[0].scope == "global"


def test_unmapped_directory_writes_global(
    paths: Paths, cfg: Config, payload: dict, tmp_path: Path
) -> None:
    summary = run(
        paths,
        cfg,
        session_file(tmp_path, cwd="/tmp/nowhere"),
        generator=FakeGenerator(payload),
        judge=FakeJudge({"scope": 0.0}),
    )
    assert summary["project"] is None
    assert facts.read_file(paths.facts_file, "global")


def test_judge_gate_prevents_generation(
    paths: Paths, cfg: Config, payload: dict, tmp_path: Path
) -> None:
    generator = FakeGenerator(payload)
    summary = run(
        paths,
        cfg,
        session_file(tmp_path),
        generator=generator,
        judge=FakeJudge({"skill": 0.1, "fact": 0.1}),
    )
    assert summary["status"] == "gated"
    assert generator.calls == []
    assert skills.list_learned(paths) == []


def test_no_judge_still_learns(paths: Paths, cfg: Config, payload: dict, tmp_path: Path) -> None:
    map_project(paths)
    summary = run(paths, cfg, session_file(tmp_path), generator=FakeGenerator(payload), judge=None)
    assert summary["status"] == "learned"
    assert summary.get("gate", True)
    assert facts.read_file(paths.facts_for("proj-a"), "proj-a")
    # The payload asked for "project" scope; the skill also asked for it. A generator
    # that asks for "global" without a judge to confirm is demoted.
    assert (paths.project_dir("proj-a") / "skills" / "stale-image-tag-triage" / "SKILL.md").exists()
    assert not (paths.learned_skills / "stale-image-tag-triage").exists()


def test_second_run_is_a_duplicate(
    paths: Paths, cfg: Config, payload: dict, tmp_path: Path
) -> None:
    file = session_file(tmp_path)
    first = run(paths, cfg, file, generator=FakeGenerator(payload), judge=FakeJudge())
    second = run(paths, cfg, file, generator=FakeGenerator(payload), judge=FakeJudge())
    assert first["status"] == "learned"
    assert second["status"] == "duplicate"


def test_force_reprocesses(paths: Paths, cfg: Config, payload: dict, tmp_path: Path) -> None:
    file = session_file(tmp_path)
    run(paths, cfg, file, generator=FakeGenerator(payload), judge=FakeJudge())
    forced = run(paths, cfg, file, generator=FakeGenerator(payload), judge=FakeJudge(), force=True)
    assert forced["status"] in {"learned", "nothing"}


def test_short_session_is_filtered(
    paths: Paths, cfg: Config, payload: dict, tmp_path: Path
) -> None:
    file = session_file(tmp_path, pairs=[("only one", "answer")], tool_calls=0)
    generator = FakeGenerator(payload)
    summary = run(paths, cfg, file, generator=generator, judge=FakeJudge())
    assert summary["status"] == "filtered"
    assert generator.calls == []


def test_feedback_makes_a_short_session_worth_reading(
    paths: Paths, cfg: Config, payload: dict, tmp_path: Path
) -> None:
    file = session_file(
        tmp_path,
        pairs=[("only one", "answer")],
        tool_calls=0,
        custom=[{"type": "custom", "customType": "rsi/feedback", "data": {"rating": "down"}}],
    )
    summary = run(paths, cfg, file, generator=FakeGenerator(payload), judge=FakeJudge())
    assert summary["status"] == "learned"


def test_retire_needs_judge_confirmation(
    paths: Paths, cfg: Config, tmp_path: Path, seeded_fact: facts.Fact
) -> None:
    payload = {
        "skill": None,
        "facts": {"retire": [{"id": seeded_fact.id, "reason": "renamed command"}]},
    }
    map_project(paths)
    denied = run(
        paths,
        cfg,
        session_file(tmp_path, session_id="s-retire-1"),
        generator=FakeGenerator(payload),
        judge=FakeJudge({"contradicted": 0.2}),
    )
    assert denied["status"] == "nothing"
    assert facts.active(facts.read_file(paths.facts_file, "global"))

    confirmed = run(
        paths,
        cfg,
        session_file(tmp_path, session_id="s-retire-2"),
        generator=FakeGenerator(payload),
        judge=FakeJudge({"contradicted": 0.95}),
    )
    assert confirmed["status"] == "learned"
    assert confirmed["facts_retired"] == [seeded_fact.id]
    assert not facts.active(facts.read_file(paths.facts_file, "global"))


def test_retire_is_ignored_without_a_judge(
    paths: Paths, cfg: Config, tmp_path: Path, seeded_fact: facts.Fact
) -> None:
    payload = {"skill": None, "facts": {"retire": [{"id": seeded_fact.id, "reason": "old"}]}}
    summary = run(paths, cfg, session_file(tmp_path), generator=FakeGenerator(payload), judge=None)
    assert summary["status"] == "nothing"
    assert facts.active(facts.read_file(paths.facts_file, "global"))


def test_secret_in_a_fact_is_refused(paths: Paths, cfg: Config, tmp_path: Path) -> None:
    payload = {"skill": None, "facts": {"add": [{"text": "token sk-abcdefghijklmnopqrstuvwx"}]}}
    summary = run(
        paths, cfg, session_file(tmp_path), generator=FakeGenerator(payload), judge=FakeJudge()
    )
    assert summary["status"] == "nothing"
    assert any("secret" in item for item in summary["refused"])


def test_secret_in_a_skill_is_refused(paths: Paths, cfg: Config, tmp_path: Path) -> None:
    payload = {
        "skill": {
            "name": "leaky",
            "description": "leaks a key",
            "body": "export KEY=sk-abcdefghijklmnopqrstuvwx",
        }
    }
    summary = run(
        paths, cfg, session_file(tmp_path), generator=FakeGenerator(payload), judge=FakeJudge()
    )
    assert summary["status"] == "nothing"
    assert skills.list_learned(paths) == []


def test_dry_run_writes_nothing(paths: Paths, cfg: Config, payload: dict, tmp_path: Path) -> None:
    map_project(paths)
    summary = run(
        paths,
        cfg,
        session_file(tmp_path),
        generator=FakeGenerator(payload),
        judge=FakeJudge(),
        dry_run=True,
    )
    assert summary["status"] == "dry_run"
    assert summary["skill"] == "stale-image-tag-triage"
    assert skills.list_learned(paths) == []
    assert not paths.facts_for("proj-a").exists()


def test_per_session_fact_limit(paths: Paths, cfg: Config, tmp_path: Path) -> None:
    tight = replace(cfg, learning=replace(cfg.learning, max_facts_per_session=1))
    payload = {
        "skill": None,
        "facts": {
            "add": [
                {"text": "The staging cluster is read-only."},
                {"text": "The production cluster is writable."},
            ]
        },
    }
    summary = run(
        paths, tight, session_file(tmp_path), generator=FakeGenerator(payload), judge=FakeJudge()
    )
    assert len(summary["facts_added"]) == 1
    assert any("per-session limit" in item for item in summary["refused"])


def test_skill_quota(paths: Paths, cfg: Config, payload: dict, tmp_path: Path) -> None:
    full = replace(cfg, learning=replace(cfg.learning, max_skills_total=0))
    summary = run(
        paths, full, session_file(tmp_path), generator=FakeGenerator(payload), judge=FakeJudge()
    )
    assert summary["skill"] is None
    assert any("quota" in item for item in summary["refused"])


def test_learning_disabled(paths: Paths, cfg: Config, payload: dict, tmp_path: Path) -> None:
    off = replace(cfg, learning=replace(cfg.learning, enabled=False))
    summary = run(
        paths, off, session_file(tmp_path), generator=FakeGenerator(payload), judge=FakeJudge()
    )
    assert summary["status"] == "disabled"


def test_eval_mode_skips_everything(
    paths: Paths, cfg: Config, payload: dict, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("RSI_EVAL", "1")
    summary = run(
        paths, cfg, session_file(tmp_path), generator=FakeGenerator(payload), judge=FakeJudge()
    )
    assert summary["status"] == "eval"
    assert skills.list_learned(paths) == []


def test_generator_failure_is_logged_not_raised(paths: Paths, cfg: Config, tmp_path: Path) -> None:
    class Broken:
        name = "broken"

        def generate(self, system, prompt):
            raise learner.GeneratorError("model unavailable")

    summary = run(paths, cfg, session_file(tmp_path), generator=Broken(), judge=FakeJudge())
    assert summary["status"] == "generator_failed"
    rows = util.read_jsonl(paths.log)
    assert rows[-1]["status"] == "generator_failed"


def test_learn_indexes_the_session(
    paths: Paths, cfg: Config, payload: dict, tmp_path: Path
) -> None:
    map_project(paths)
    run(paths, cfg, session_file(tmp_path), generator=FakeGenerator(payload), judge=FakeJudge())
    con = store.connect(paths.db)
    try:
        row = con.execute("SELECT project, tool_calls FROM sessions").fetchone()
        assert row["project"] == "proj-a"
        assert row["tool_calls"] == 3
        assert store.stats(con)["turns"] == 1
    finally:
        con.close()


def test_log_records_the_outcome(paths: Paths, cfg: Config, payload: dict, tmp_path: Path) -> None:
    run(paths, cfg, session_file(tmp_path), generator=FakeGenerator(payload), judge=FakeJudge())
    row = util.read_jsonl(paths.log)[-1]
    assert row["status"] == "learned"
    assert row["session"] == "s-0001"
    assert row["fingerprint"]
