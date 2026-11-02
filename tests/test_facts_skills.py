from __future__ import annotations

import pytest

from pi_rsi import facts, skills
from pi_rsi.paths import Paths


def test_add_and_read(paths: Paths) -> None:
    result = facts.add(paths, "SQLite must be 3.34 or newer for the trigram tokenizer.")
    assert result.fact is not None
    assert result.fact.id.startswith("f-")
    stored = facts.read_file(paths.facts_file, "global")
    assert [fact.text for fact in stored] == [
        "SQLite must be 3.34 or newer for the trigram tokenizer."
    ]


def test_add_is_idempotent_for_near_duplicates(paths: Paths) -> None:
    first = facts.add(paths, "The deploy uses the merge commit image tag.")
    second = facts.add(paths, "the deploy uses the merge commit image tag")
    assert second.duplicate_of == first.fact.id
    assert len(facts.read_file(paths.facts_file, "global")) == 1


def test_add_rejects_empty_and_oversized(paths: Paths) -> None:
    assert facts.add(paths, "   ").errors
    assert facts.add(paths, "x" * 500).errors


def test_scope_uses_project_file(paths: Paths) -> None:
    facts.add(paths, "The staging cluster is read-only.", scope="proj-a")
    assert paths.facts_for("proj-a").exists()
    assert not paths.facts_file.exists()
    entries = facts.load_all(paths)
    assert entries[0].scope == "proj-a"


def test_visible_is_global_plus_project(paths: Paths) -> None:
    facts.add(paths, "Global truth.")
    facts.add(paths, "Project truth.", scope="proj-a")
    facts.add(paths, "Other project truth.", scope="proj-b")
    texts = {fact.text for fact in facts.visible(paths, "proj-a")}
    assert texts == {"Global truth.", "Project truth."}


def test_retire_keeps_history(paths: Paths, seeded_fact: facts.Fact) -> None:
    retired = facts.retire(paths, seeded_fact.id, "the command was renamed")
    assert retired.status == facts.STATUS_RETIRED
    stored = facts.read_file(paths.facts_file, "global")
    assert len(stored) == 1
    assert stored[0].reason == "the command was renamed"
    assert facts.active(stored) == []


def test_retire_rejects_bad_ids(paths: Paths) -> None:
    with pytest.raises(facts.FactError):
        facts.retire(paths, "nope", "x")
    with pytest.raises(facts.FactError, match="no such fact"):
        facts.retire(paths, "f-00000000", "x")


def test_render_is_bounded_and_names_ids(paths: Paths, seeded_fact: facts.Fact) -> None:
    facts.add(paths, "The second fact is long enough to overflow a tiny budget.")
    block = facts.render(facts.read_file(paths.facts_file, "global"), limit_chars=60)
    assert seeded_fact.id in block
    assert "more facts not shown" in block
    assert facts.render([]) == ""


def test_write_is_atomic_and_private(paths: Paths) -> None:
    facts.add(paths, "one")
    mode = paths.facts_file.stat().st_mode & 0o777
    assert mode == 0o600
    assert not list(paths.facts_file.parent.glob("*.tmp"))


def test_corrupt_lines_are_skipped(paths: Paths) -> None:
    paths.facts_file.write_text('{"id": "f-11111111", "text": "ok"}\nnot json\n', encoding="utf-8")
    assert [fact.text for fact in facts.read_file(paths.facts_file, "global")] == ["ok"]


def test_counts_today(paths: Paths) -> None:
    facts.add(paths, "one")
    assert facts.counts_today(paths, "global") == 1


def test_skill_write_and_list(paths: Paths) -> None:
    file = skills.write(
        paths,
        name="flaky-test-triage",
        description="Use when a test fails intermittently in CI",
        body="1. Re-run the test in isolation.\n2. Check for shared state.",
    )
    assert file == paths.learned_skills / "flaky-test-triage" / "SKILL.md"
    text = file.read_text(encoding="utf-8")
    assert text.startswith("---\nname: flaky-test-triage\n")
    listed = skills.list_learned(paths)
    assert [skill.name for skill in listed] == ["flaky-test-triage"]
    assert listed[0].description.startswith("Use when")


def test_skill_validation() -> None:
    assert skills.validate("Bad Name", "d", "b")
    assert skills.validate("-bad", "d", "b")
    assert skills.validate("a--b", "d", "b")
    assert skills.validate("ok-name", "", "b") == ["description is required"]
    assert skills.validate("ok-name", "d" * 2000, "b")
    assert skills.validate("ok-name", "d", "b" * 7000)
    assert skills.validate("ok-name", "d", "b\n" * 200)
    assert skills.validate("ok-name", "fine", "fine") == []


def test_skill_refuses_secrets() -> None:
    errors = skills.validate("leaky", "d", "export KEY=sk-abcdefghijklmnopqrstuvwx")
    assert any("secret" in error for error in errors)


def test_skill_overwrite_takes_a_snapshot(paths: Paths) -> None:
    skills.write(paths, name="alpha", description="first", body="one")
    skills.write(paths, name="alpha", description="second", body="two")
    snapshots = list(paths.snapshots.rglob("SKILL.md"))
    assert len(snapshots) == 1
    assert "first" in snapshots[0].read_text(encoding="utf-8")
    assert "second" in (paths.learned_skills / "alpha" / "SKILL.md").read_text(encoding="utf-8")


def test_skill_scopes_are_separate(paths: Paths) -> None:
    skills.write(paths, name="alpha", description="global", body="one")
    skills.write(paths, name="beta", description="project", body="two", scope="proj-a")
    assert [skill.name for skill in skills.list_learned(paths, scope="global")] == ["alpha"]
    assert [skill.name for skill in skills.list_learned(paths, scope="proj-a")] == ["beta"]
    assert {skill.name for skill in skills.list_learned(paths)} == {"alpha", "beta"}


def test_skill_no_overwrite_mode(paths: Paths) -> None:
    skills.write(paths, name="alpha", description="first", body="one")
    with pytest.raises(skills.SkillError, match="already exists"):
        skills.write(paths, name="alpha", description="second", body="two", overwrite=False)


def test_skill_remove(paths: Paths) -> None:
    skills.write(paths, name="alpha", description="first", body="one")
    assert skills.remove(paths, "alpha") is True
    assert skills.remove(paths, "alpha") is False
    assert list(paths.snapshots.rglob("SKILL.md"))


def test_skill_parse_without_frontmatter(paths: Paths) -> None:
    meta, body = skills.parse("# title\n\ntext")
    assert meta == {}
    assert body == "# title\n\ntext"


def test_skill_body_survives_colons(paths: Paths) -> None:
    skills.write(paths, name="alpha", description="has: a colon", body="step: one")
    skill = skills.list_learned(paths)[0]
    assert skill.description == "has: a colon"
    assert skill.body == "step: one"
