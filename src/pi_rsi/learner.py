"""The learning loop.

    index -> filter -> judge gate -> generate -> judge review -> validate -> write -> log

Every stage can say no, and most sessions end at a no. That is the point: the expensive
part (one generator call) only runs when a cheap judge thinks the session contains
something reusable, and everything the generator proposes is checked again before it
reaches a prompt.

The loop is written so that it degrades instead of collapsing:

* no judge configured  -> the gate opens, the generator decides, scope is chosen from
                          the session (project when the directory maps to one);
* judge configured but failing -> the run proceeds, the failure is logged;
* generator failing    -> nothing is written, the reason is logged.

Only the writes are irreversible, so they are the part with the most checks: secret
scan, quotas, dedupe, and a snapshot for a skill that replaces an existing one.
"""

from __future__ import annotations

import difflib
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import facts, guard, judges, projects, session, skills, store, util
from .config import Config, ConfigError
from .generators import Generator, GeneratorError, parse_output
from .generators import build as build_generator
from .paths import GLOBAL_SCOPE, Paths

SYSTEM = "You curate a memory for a coding agent. Reply with one JSON object only."
PROMPT_PATH = Path(__file__).with_name("prompts") / "learn.md"
MAX_TURN_CHARS_IN_DIGEST = 1500
NEAR_DUPLICATE_RATIO = 0.6


@dataclass(slots=True)
class Candidate:
    text: str
    scope: str


@dataclass(slots=True)
class SkillCandidate:
    name: str
    description: str
    body: str
    scope: str


@dataclass(slots=True)
class Plan:
    skill: SkillCandidate | None = None
    add: list[Candidate] = field(default_factory=list)
    retire: list[tuple[str, str]] = field(default_factory=list)
    supersede: list[tuple[str, Candidate]] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    reason: str = ""


def learn(
    paths: Paths,
    session_file: str | Path,
    cfg: Config,
    *,
    force: bool = False,
    generator: Generator | None = None,
    judge: judges.Judge | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Learn from one session file. Never raises for an expected failure."""
    paths.ensure()
    path = Path(session_file).expanduser()
    base: dict[str, Any] = {"ts": util.stamp(now), "session": path.stem, "file": str(path)}
    try:
        with util.locked(paths.lock):
            summary = _run(
                paths,
                path,
                cfg,
                force=force,
                generator=generator,
                judge=judge,
                now=now,
                dry_run=dry_run,
            )
    except util.LockedError as exc:
        summary = {**base, "status": "locked", "reason": str(exc)}
        _log(paths, summary)
        return summary
    except (ConfigError, GeneratorError, judges.JudgeError) as exc:
        summary = {**base, "status": "error", "reason": str(exc)}
        _log(paths, summary)
        return summary
    summary = {**base, **summary}
    if not dry_run:
        _log(paths, summary)
    return summary


def _run(
    paths: Paths,
    path: Path,
    cfg: Config,
    *,
    force: bool,
    generator: Generator | None,
    judge: judges.Judge | None,
    now: datetime | None,
    dry_run: bool,
) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "error", "reason": f"no such session file: {path}"}
    rules = guard.load_rules(paths.home / "guard.json")
    parsed = session.parse(path, max_chars=cfg.retrieval.max_turn_chars, rules=rules)
    project = projects.resolve(parsed.record.cwd, mapping=projects.load(paths))
    parsed.record.project = project

    con = store.connect(paths.db)
    try:
        store.index_session(con, parsed.record, parsed.turns)
    finally:
        con.close()

    fingerprint = f"{path.stat().st_mtime_ns}|{len(parsed.turns)}|{parsed.record.tool_calls}"
    summary: dict[str, Any] = {
        "session": parsed.record.id,
        "project": project,
        "turns": len(parsed.turns),
        "tool_calls": parsed.record.tool_calls,
        "fingerprint": fingerprint,
    }

    if parsed.skipped:
        return {**summary, "status": "nothing", "reason": "session has no user turns"}
    if not cfg.learning.enabled:
        return {**summary, "status": "disabled", "reason": "learning is disabled"}
    if os.environ.get("RSI_EVAL") == "1":
        return {**summary, "status": "eval", "reason": "RSI_EVAL=1: no reads or writes"}
    if not force and _seen(paths, parsed.record.id, fingerprint):
        return {**summary, "status": "duplicate", "reason": "this session was already processed"}
    if not force and not _worth(parsed, cfg):
        return {
            **summary,
            "status": "filtered",
            "reason": "too short to hold a reusable lesson",
        }

    judge_error = ""
    if judge is not None:
        try:
            gate = judge.ask(
                {
                    "skill": judges.noul(
                        "This session contains a procedure that took real work to find, would "
                        "save comparable work in a future session, and is not already written "
                        "down in the repository or its documentation."
                    ),
                    "fact": judges.noul(
                        "This session established at least one durable, verifiable fact (a "
                        "version, identifier, constraint, endpoint or decision with a reason) "
                        "that is not already recorded and that a future session would need."
                    ),
                }
            )
            skill_ok = judges.probability(gate, "skill") >= cfg.judge.threshold("gate_skill")
            fact_ok = judges.probability(gate, "fact") >= cfg.judge.threshold("gate_fact")
            summary["gate"] = {
                "skill": round(judges.probability(gate, "skill"), 3),
                "fact": round(judges.probability(gate, "fact"), 3),
            }
            if not skill_ok and not fact_ok:
                return {**summary, "status": "gated", "reason": "the judge saw nothing durable"}
        except judges.JudgeError as exc:
            judge = None
            judge_error = str(exc)
            summary["judge_error"] = judge_error

    generator = generator or build_generator(cfg.generator)
    existing_facts = facts.visible(paths, project)
    learned = skills.list_learned(paths)
    prompt = _prompt(
        project=project,
        digest=_digest(parsed),
        facts_block=facts.render(existing_facts, limit_chars=3000),
        skills_block=skills.render_index(learned),
    )
    try:
        raw = generator.generate(SYSTEM, prompt)
    except GeneratorError as exc:
        return {**summary, "status": "generator_failed", "reason": str(exc)}
    data = parse_output(raw)
    plan = _plan(data, project=project)
    summary["reason"] = plan.reason
    summary["dropped"] = plan.dropped

    if judge is not None:
        _review(plan, judge, cfg, plan_existing=existing_facts, project=project)
    else:
        # Without a judge the conservative scope wins: a global fact follows the agent
        # into every repository, and only a second opinion should be able to do that.
        if plan.skill is not None:
            plan.skill.scope = project or GLOBAL_SCOPE
        for candidate in plan.add:
            candidate.scope = project or GLOBAL_SCOPE
        for fact_id, _ in plan.retire:
            plan.dropped.append(f"retire {fact_id}: needs a judge to confirm")
        for fact_id, _ in plan.supersede:
            plan.dropped.append(f"supersede {fact_id}: needs a judge to confirm")
        plan.retire = []
        plan.supersede = []

    written = _write(paths, plan, cfg, project=project, now=now, dry_run=dry_run, rules=rules)
    summary.update(written)
    status = (
        "learned"
        if any(written.get(key) for key in ("skill", "facts_added", "facts_retired"))
        else "nothing"
    )
    if dry_run:
        status = "dry_run"
    return {**summary, "status": status}


def _prompt(*, project: str | None, digest: str, facts_block: str, skills_block: str) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    sections = [
        f"## Project\n\n{project or '(no project: the working directory is not mapped to one)'}",
        f"## Recorded facts\n\n{facts_block or '(none)'}",
        f"## Existing skills\n\n{skills_block or '(none)'}",
        f"## Session digest\n\n{digest}",
    ]
    return template + "\n\n---\n\n" + "\n\n".join(sections)


def _digest(parsed: session.ParsedSession) -> str:
    lines: list[str] = []
    for turn in parsed.turns:
        lines.append(f"### turn {turn.turn}\n{util.clip(turn.text, MAX_TURN_CHARS_IN_DIGEST)}")
    for entry in parsed.feedback:
        rating = entry.get("rating") or entry.get("value") or "?"
        reason = entry.get("reason") or ""
        lines.append(f"### feedback\n{rating} {reason}".strip())
    return "\n\n".join(lines)


def _worth(parsed: session.ParsedSession, cfg: Config) -> bool:
    if parsed.feedback:
        return True
    return (
        parsed.record.tool_calls >= cfg.learning.min_tool_calls
        or parsed.record.user_turns > cfg.learning.min_user_turns
    )


def _plan(data: dict[str, Any], *, project: str | None) -> Plan:
    plan = Plan()
    reason = data.get("reason")
    if isinstance(reason, str):
        plan.reason = util.clip(reason, 300)
    skill_raw = data.get("skill")
    if isinstance(skill_raw, dict):
        name = str(skill_raw.get("name") or "").strip()
        description = str(skill_raw.get("description") or "").strip()
        body = str(skill_raw.get("body") or "").strip()
        scope = _scope(skill_raw.get("scope"), project)
        if name and description and body:
            plan.skill = SkillCandidate(name=name, description=description, body=body, scope=scope)
        else:
            plan.dropped.append("skill: incomplete (name, description and body are required)")
    fact_block = data.get("facts")
    if isinstance(fact_block, dict):
        for entry in _as_list(fact_block.get("add")):
            if isinstance(entry, dict):
                text = str(entry.get("text") or "").strip()
                scope = _scope(entry.get("scope"), project)
            else:
                text = str(entry).strip()
                scope = project or GLOBAL_SCOPE
            if text:
                plan.add.append(Candidate(text=text, scope=scope))
        for entry in _as_list(fact_block.get("retire")):
            if isinstance(entry, dict) and entry.get("id"):
                plan.retire.append(
                    (str(entry["id"]), util.clip(str(entry.get("reason") or ""), 200))
                )
        for entry in _as_list(fact_block.get("supersede")):
            if isinstance(entry, dict) and entry.get("id"):
                text = str(entry.get("text") or "").strip()
                if text:
                    plan.supersede.append(
                        (
                            str(entry["id"]),
                            Candidate(text=text, scope=_scope(entry.get("scope"), project)),
                        )
                    )
    return plan


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _scope(value: Any, project: str | None) -> str:
    scope = str(value or "").strip()
    if scope == GLOBAL_SCOPE:
        return GLOBAL_SCOPE
    if scope and project and scope == project:
        return project
    return project or GLOBAL_SCOPE


def _review(
    plan: Plan,
    judge: judges.Judge,
    cfg: Config,
    *,
    plan_existing: list[facts.Fact],
    project: str | None,
) -> None:
    """Second judge pass: scope, durability, duplicates, retirements.

    Without a judge only the deterministic checks survive, which is why retirement is
    never inferred from a single generator call.
    """
    if plan.skill is not None:
        try:
            answers = judge.ask(
                {
                    "scope": judges.noul(
                        "This procedure holds in every repository and every language, not only "
                        "in the one this session worked on."
                    )
                }
            )
            plan.skill.scope = (
                GLOBAL_SCOPE
                if judges.probability(answers, "scope") >= cfg.judge.threshold("scope_global")
                else project or GLOBAL_SCOPE
            )
        except judges.JudgeError:
            plan.skill.scope = project or GLOBAL_SCOPE

    threshold_global = cfg.judge.threshold("scope_global")
    threshold_durable = cfg.judge.threshold("fact_durable")
    threshold_contradicted = cfg.judge.threshold("fact_contradicted")
    threshold_duplicate = cfg.judge.threshold("fact_supersedes")

    by_id = {fact.id: fact for fact in plan_existing}
    kept: list[Candidate] = []
    for candidate in plan.add:
        questions: dict[str, dict[str, Any]] = {
            "durable": judges.noul(
                "This statement will still be true in a month and is worth carrying into "
                f"unrelated future sessions: {candidate.text}"
            ),
            "global": judges.noul(
                "This statement is true for every repository, not only the one it came from: "
                f"{candidate.text}"
            ),
        }
        similar = _most_similar(candidate.text, by_id.values())
        if similar is not None:
            questions["duplicate"] = judges.noul(
                f"The recorded fact {similar.id} already says the same thing: {similar.text}"
            )
        try:
            answers = judge.ask(questions)
        except judges.JudgeError:
            kept.append(candidate)
            continue
        if judges.probability(answers, "durable") < threshold_durable:
            plan.dropped.append(f"fact: not durable: {util.clip(candidate.text, 80)}")
            continue
        if similar is not None and judges.probability(answers, "duplicate") >= threshold_duplicate:
            plan.dropped.append(f"fact: duplicate of {similar.id}")
            continue
        if project and judges.probability(answers, "global") >= threshold_global:
            candidate.scope = GLOBAL_SCOPE
        else:
            candidate.scope = project or GLOBAL_SCOPE
        kept.append(candidate)
    plan.add = kept

    confirmed_retire: list[tuple[str, str]] = []
    for fact_id, reason in plan.retire:
        fact = by_id.get(fact_id)
        if fact is None:
            plan.dropped.append(f"retire: unknown fact {fact_id}")
            continue
        try:
            answers = judge.ask(
                {
                    "contradicted": judges.noul(
                        "This session shows that the recorded fact is no longer true: "
                        f"{fact.id}: {fact.text}"
                    )
                }
            )
        except judges.JudgeError:
            plan.dropped.append(f"retire {fact_id}: the judge failed, keeping the fact")
            continue
        if judges.probability(answers, "contradicted") >= threshold_contradicted:
            confirmed_retire.append((fact_id, reason or "contradicted by a later session"))
        else:
            plan.dropped.append(f"retire {fact_id}: not confirmed")
    plan.retire = confirmed_retire

    confirmed_supersede: list[tuple[str, Candidate]] = []
    for fact_id, candidate in plan.supersede:
        fact = by_id.get(fact_id)
        if fact is None:
            plan.dropped.append(f"supersede: unknown fact {fact_id}")
            continue
        try:
            answers = judge.ask(
                {
                    "replaces": judges.noul(
                        "The new text is a correction of the recorded fact and should replace "
                        f"it. Recorded: {fact.id}: {fact.text}\nNew: {candidate.text}"
                    )
                }
            )
        except judges.JudgeError:
            plan.dropped.append(f"supersede {fact_id}: the judge failed, keeping the fact")
            continue
        if judges.probability(answers, "replaces") >= threshold_contradicted:
            plan.retire.append((fact_id, "superseded"))
            plan.add.append(candidate)
            confirmed_supersede.append((fact_id, candidate))
        else:
            plan.dropped.append(f"supersede {fact_id}: not confirmed")
    plan.supersede = confirmed_supersede


def _most_similar(text: str, candidates: Any) -> facts.Fact | None:
    best: tuple[float, facts.Fact] | None = None
    for fact in candidates:
        if not facts.near_duplicate(text, fact.text):
            continue
        ratio = difflib.SequenceMatcher(
            None, facts.normalize(text), facts.normalize(fact.text)
        ).ratio()
        if best is None or ratio > best[0]:
            best = (ratio, fact)
    return best[1] if best else None


def _write(
    paths: Paths,
    plan: Plan,
    cfg: Config,
    *,
    project: str | None,
    now: datetime | None,
    dry_run: bool,
    rules: tuple[guard.Rule, ...],
) -> dict[str, Any]:
    result: dict[str, Any] = {"skill": None, "facts_added": [], "facts_retired": [], "refused": []}

    if plan.skill is not None:
        errors = skills.validate(
            plan.skill.name, plan.skill.description, plan.skill.body, rules=rules
        )
        if errors:
            result["refused"].append(f"skill {plan.skill.name}: {'; '.join(errors)}")
        elif not _skill_budget_ok(paths, cfg, now):
            result["refused"].append(f"skill {plan.skill.name}: quota reached")
        elif dry_run:
            result["skill"] = plan.skill.name
        else:
            try:
                path = skills.write(
                    paths,
                    name=plan.skill.name,
                    description=plan.skill.description,
                    body=plan.skill.body,
                    scope=plan.skill.scope,
                    rules=rules,
                )
                result["skill"] = str(path)
            except skills.SkillError as exc:
                result["refused"].append(f"skill {plan.skill.name}: {exc}")

    for fact_id, reason in plan.retire:
        if dry_run:
            result["facts_retired"].append(fact_id)
            continue
        try:
            facts.retire(paths, fact_id, reason, now=now)
            result["facts_retired"].append(fact_id)
        except facts.FactError as exc:
            result["refused"].append(f"retire {fact_id}: {exc}")

    budget = cfg.learning.max_facts_per_session
    for candidate in plan.add:
        if budget <= 0:
            result["refused"].append(
                f"fact: per-session limit reached, dropped {candidate.text[:60]!r}"
            )
            continue
        rule = guard.find_secret(candidate.text, rules)
        if rule is not None:
            result["refused"].append(f"fact matches the secret pattern {rule!r}; dropped")
            continue
        if not _fact_budget_ok(paths, cfg, candidate.scope, now):
            result["refused"].append(f"fact {candidate.scope}: quota reached, dropped")
            continue
        if dry_run:
            result["facts_added"].append(candidate.text)
            budget -= 1
            continue
        added = facts.add(paths, candidate.text, scope=candidate.scope, source="learn", now=now)
        if added.errors:
            result["refused"].extend(f"fact: {error}" for error in added.errors)
            continue
        budget -= 1
        result["facts_added"].append(added.fact.id if added.fact else "")
    return result


def _skill_budget_ok(paths: Paths, cfg: Config, now: datetime | None) -> bool:
    learned = skills.list_learned(paths)
    if len(learned) >= cfg.learning.max_skills_total:
        return False
    today = util.utcnow(now).date()
    written_today = sum(
        1 for skill in learned if datetime.fromtimestamp(skill.mtime, UTC).date() == today
    )
    return written_today < cfg.learning.max_skills_per_day


def _fact_budget_ok(paths: Paths, cfg: Config, scope: str, now: datetime | None) -> bool:
    path = paths.facts_for(scope)
    existing = facts.read_file(path, scope)
    active = sum(1 for fact in existing if fact.status == facts.STATUS_ACTIVE)
    if active >= cfg.learning.max_facts_per_scope:
        return False
    return facts.counts_today(paths, scope, now=now) < cfg.learning.max_facts_per_day


def _seen(paths: Paths, session_id: str, fingerprint: str) -> bool:
    for row in reversed(util.read_jsonl(paths.log)[-200:]):
        if row.get("session") == session_id and row.get("fingerprint") == fingerprint:
            return row.get("status") not in {"error", "locked", "generator_failed", "eval"}
    return False


def _log(paths: Paths, summary: dict[str, Any]) -> None:
    util.append_jsonl(paths.log, summary)
