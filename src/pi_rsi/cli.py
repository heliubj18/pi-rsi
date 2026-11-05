"""Command-line interface.

This module is the contract between the TypeScript extensions and the Python core. The
extensions never import Python and never touch SQLite; they spawn::

    python3 -I <package>/src/pi_rsi/cli.py recall --query ... --cwd ... --json

so the two halves can be upgraded independently and a crash in the learner cannot take
down pi. Every subcommand exits non-zero on failure and prints one line to stderr.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path
from typing import Any

from . import context, doctor, episodes, facts, learner, projects, skills, store
from . import generators as generator_module
from . import judges as judge_module
from .config import ConfigError, load, render
from .paths import Paths
from .util import read_jsonl

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_STATE = 3

CONFIG_TEMPLATE = """\
# pi-rsi configuration.
#
# Two independent adapters. The defaults need nothing: an extra `pi` process writes the
# candidates, and nothing judges them. Adding a judge makes the loop cheaper (one
# judgement call decides whether the expensive generator call happens at all) and makes
# what it learns more precise.

[generator]
# pi | openai | anthropic
provider = "pi"
# Empty means pi's configured default model. Use any id `pi --model` accepts.
model = ""
# Required for openai/anthropic: the base URL, without a trailing slash.
base_url = ""
# "env:NAME", "file:PATH" or a literal.
api_key = ""
timeout_s = 180

[judge]
# none | systemone | openai | anthropic
# systemone speaks the small JSON protocol described in src/pi_rsi/judges/systemone.py;
# openai and anthropic speak their chat APIs and answer the same question set.
provider = "none"
base_url = ""
# Only systemone: the path under base_url.
path = "/v3/decisions"
model = ""
api_key = ""
timeout_s = 30

[judge.thresholds]
# Gate: if both are below their threshold, no generator call happens.
gate_skill = 0.5
gate_fact = 0.5
# Facts.
fact_durable = 0.5
fact_contradicted = 0.8
fact_supersedes = 0.6
# A lesson is global only above this confidence.
scope_global = 0.8
# Reserved for v0.2; accepted now so a config survives the upgrade.
skill_blamed = 0.5
recall_relevant = 0.5
recall_inject = 0.8
recall_recent = 0.6

[learning]
enabled = true
# Keep the prompt small and the lessons reviewable.
max_skills_total = 30
max_skills_per_day = 3
max_facts_per_session = 3
max_facts_per_day = 10
max_facts_per_scope = 40
digest_chars = 24000
min_tool_calls = 3
min_user_turns = 2

[retrieval]
candidates = 20
recent = 8
max_turn_chars = 6000
max_inject_chars = 4000

[sessions]
# Where pi writes sessions. Empty means ~/.pi/agent/sessions.
dir = ""
"""

PROJECTS_TEMPLATE = """{
  "my-project": ["~/src/my-project"]
}
"""


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    parser = _parser()
    args = parser.parse_args(argv)
    paths = Paths.resolve(args.home)
    try:
        return _dispatch(args, paths)
    except ConfigError as exc:
        return _fail(f"configuration: {exc}", EXIT_USAGE)
    except (store.StoreError, facts.FactError, skills.SkillError) as exc:
        return _fail(str(exc), EXIT_STATE)
    except BrokenPipeError:  # pragma: no cover - piping into head
        return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rsi",
        description="Self-improvement loop for the pi coding agent.",
    )
    parser.add_argument("--home", help="override $RSI_HOME for this command")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create the home directory and a config file")
    init.add_argument(
        "--force", action="store_true", help="overwrite config.toml and projects.json"
    )

    check = sub.add_parser("doctor", help="check that the loop can run here")
    check.add_argument("--probe", action="store_true", help="ask the judge one trivial question")
    check.add_argument("--json", action="store_true")

    sub.add_parser("config", help="print the effective configuration")

    ctx = sub.add_parser("context", help="the facts and project injected into a session")
    ctx.add_argument("--cwd", default=None)
    ctx.add_argument("--json", action="store_true")

    project = sub.add_parser("project", help="resolve a directory to a project")
    project.add_argument("--cwd", default=None)

    rec = sub.add_parser("recall", help="search past sessions")
    rec.add_argument("query", nargs="?", default="")
    rec.add_argument("--cwd", default=None)
    rec.add_argument("--project", default=None)
    rec.add_argument("--limit", type=int, default=None)
    rec.add_argument("--recent", type=int, default=None)
    rec.add_argument("--sort", choices=["relevance", "recent"], default="relevance")
    rec.add_argument("--all-projects", action="store_true")
    rec.add_argument("--json", action="store_true")

    index = sub.add_parser("index", help="index session files")
    index.add_argument("files", nargs="*")
    index.add_argument(
        "--all", action="store_true", help="index every file in the sessions directory"
    )
    index.add_argument("--dir", default=None, help="index every file in this directory instead")
    index.add_argument(
        "--force", action="store_true", help="re-index files that are already current"
    )
    index.add_argument("--json", action="store_true")

    reindex = sub.add_parser("reindex", help="rebuild the whole index from session files")
    reindex.add_argument("--dir", default=None)
    reindex.add_argument("--json", action="store_true")

    learn = sub.add_parser("learn", help="learn from one session file")
    learn.add_argument("file", nargs="?", default=None)
    learn.add_argument(
        "--drain",
        action="store_true",
        help="learn from every queued request (what the extension starts); takes no file",
    )
    learn.add_argument(
        "--wait",
        type=float,
        default=None,
        help="seconds to wait for another run to finish (default 900); 0 gives up at once",
    )
    learn.add_argument(
        "--force", action="store_true", help="ignore the duplicate and length checks"
    )
    learn.add_argument("--dry-run", action="store_true", help="decide and validate, write nothing")
    learn.add_argument("--model", default=None, help="override generator.model for this run")
    learn.add_argument("--no-judge", action="store_true", help="skip the judge even if configured")
    learn.add_argument("--json", action="store_true")

    fact = sub.add_parser("facts", help="list recorded facts")
    fact.add_argument("--all", action="store_true", help="include retired facts")
    fact.add_argument("--json", action="store_true")

    forget = sub.add_parser("forget", help="retire a fact")
    forget.add_argument("id")
    forget.add_argument("--reason", default="retired by the user")

    skilled = sub.add_parser("skills", help="list learned skills")
    skilled.add_argument("--json", action="store_true")

    log = sub.add_parser("log", help="show recent learning runs")
    log.add_argument("-n", type=int, default=20)
    log.add_argument("--json", action="store_true")

    stats = sub.add_parser("stats", help="counts for the index, facts, skills and runs")
    stats.add_argument("--json", action="store_true")
    return parser


def _dispatch(args: argparse.Namespace, paths: Paths) -> int:
    handler = {
        "init": _init,
        "doctor": _doctor,
        "config": _config,
        "context": _context,
        "project": _project,
        "recall": _recall,
        "index": _index,
        "reindex": _reindex,
        "learn": _learn,
        "facts": _facts,
        "forget": _forget,
        "skills": _skills,
        "log": _log,
        "stats": _stats,
    }[args.command]
    return handler(args, paths)


def _init(args: argparse.Namespace, paths: Paths) -> int:
    paths.ensure()
    config_path = paths.config
    if config_path.exists() and not args.force:
        print(f"kept {config_path}")
    else:
        config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        print(f"wrote {config_path}")
    projects_path = paths.projects_file
    if projects_path.exists() and not args.force:
        print(f"kept {projects_path}")
    else:
        projects_path.write_text(PROJECTS_TEMPLATE, encoding="utf-8")
        print(f"wrote {projects_path}")
    print(
        "\nNext:\n"
        f"  1. replace the example in {projects_path} with your own repositories\n"
        f"  2. set [generator] and [judge] in {config_path}\n"
        "  3. run `rsi doctor`, then `rsi index --all`"
    )
    return EXIT_OK


def _doctor(args: argparse.Namespace, paths: Paths) -> int:
    checks = doctor.run(paths, probe=args.probe)
    if args.json:
        _json({"checks": [check.as_dict() for check in checks]})
    else:
        print(doctor.report(checks))
    return EXIT_FAIL if doctor.failed(checks) else EXIT_OK


def _config(args: argparse.Namespace, paths: Paths) -> int:
    print(render(load(paths)))
    return EXIT_OK


def _context(args: argparse.Namespace, paths: Paths) -> int:
    cfg = load(paths)
    payload = context.build(paths, cfg, cwd=args.cwd)
    if args.json:
        _json(payload)
    else:
        print(context.render_text(payload) or "(nothing to inject)")
    return EXIT_OK


def _project(args: argparse.Namespace, paths: Paths) -> int:
    name = episodes.project_for(paths, args.cwd)
    print(name or "(unmapped)")
    return EXIT_OK


def _recall(args: argparse.Namespace, paths: Paths) -> int:
    cfg = load(paths)
    if not paths.db.exists():
        if args.json:
            _json({"query": args.query, "hits": []})
        else:
            print("no index yet: run `rsi index --all`")
        return EXIT_OK
    con = store.connect(paths.db, readonly=True)
    try:
        hits = episodes.recall(
            con,
            args.query,
            paths=paths,
            cfg=cfg,
            cwd=args.cwd,
            project=args.project,
            limit=args.limit,
            recent=args.recent,
            sort=args.sort,
            cross_project=args.all_projects,
        )
    finally:
        con.close()
    if args.json:
        _json(
            {
                "query": args.query,
                "project": args.project or episodes.project_for(paths, args.cwd),
                "hits": [
                    {
                        **hit.as_dict(),
                        "section": f"session {hit.session} turn {hit.turn}"
                        + (f" ({hit.project})" if hit.project else ""),
                    }
                    for hit in hits
                ],
            }
        )
    else:
        for hit in hits:
            print(
                f"{hit.relevance:7.3f}  {hit.ts or '?'}  {hit.project or '-'}  {hit.session}#{hit.turn}"
            )
            print("         " + hit.text.replace("\n", "\n         ")[:1200])
    return EXIT_OK


def _index(args: argparse.Namespace, paths: Paths) -> int:
    cfg = load(paths)
    con = store.connect(paths.db)
    try:
        if args.all or args.dir:
            result = episodes.index_all(
                con,
                paths=paths,
                cfg=cfg,
                directory=Path(args.dir) if args.dir else None,
                force=args.force,
            )
            payload = {"mode": "directory", **result.as_dict()}
        elif args.files:
            results = [
                {
                    "file": str(file),
                    "turns": episodes.index_file(
                        con, Path(file).expanduser(), paths=paths, cfg=cfg, force=args.force
                    ),
                }
                for file in args.files
            ]
            payload = {"mode": "files", "results": results}
        else:
            return _fail("index needs a file, --all or --dir", EXIT_USAGE)
    finally:
        con.close()
    if args.json:
        _json(payload)
    else:
        if payload.get("mode") == "directory":
            print(
                f"{payload['indexed']} indexed, {payload['skipped']} up to date, "
                f"{payload['turns']} turns, {payload['files']} files"
            )
            for error in payload.get("errors", []):
                print(f"error: {error}", file=sys.stderr)
        else:
            for result in payload["results"]:
                print(f"{result['turns']:5d}  {result['file']}")
    return EXIT_OK


def _reindex(args: argparse.Namespace, paths: Paths) -> int:
    cfg = load(paths)
    con = store.connect(paths.db)
    try:
        result = episodes.index_all(
            con, paths=paths, cfg=cfg, directory=Path(args.dir) if args.dir else None, force=True
        )
    finally:
        con.close()
    if args.json:
        _json(result.as_dict())
    else:
        print(f"rebuilt: {result.indexed} sessions, {result.turns} turns")
        for error in result.errors or []:
            print(f"error: {error}", file=sys.stderr)
    return EXIT_OK


def _learn(args: argparse.Namespace, paths: Paths) -> int:
    """Learn from one session, or (``--drain``) from every queued request.

    A plain run is enqueued first and then drains the queue, so a request is never lost
    to another run holding the lock: if this run cannot get the lock in time, its request
    stays queued and the next drain picks it up. ``--dry-run``, ``--no-judge`` and
    ``--model`` change how one session is judged, so they bypass the queue.
    """
    cfg = load(paths)
    wait_s = learner.LOCK_WAIT_S if args.wait is None else max(0.0, args.wait)
    judge = None if args.no_judge else judge_module.build(cfg.judge)
    if args.drain:
        if args.file:
            return _fail("learn --drain takes no file", EXIT_USAGE)
        return _report_drain(learner.drain(paths, cfg, judge=judge, wait_s=wait_s), args)
    if not args.file:
        return _fail("learn needs a session file, or --drain", EXIT_USAGE)
    file = Path(args.file).expanduser()

    if args.dry_run or args.no_judge or args.model:
        generator = None
        if args.model:
            from dataclasses import replace

            generator = generator_module.build(replace(cfg.generator, model=args.model))
        summary = learner.learn(
            paths,
            file,
            cfg,
            force=args.force,
            generator=generator,
            judge=judge,
            dry_run=args.dry_run,
            wait_s=wait_s,
        )
        return _report_one(summary, args)

    learner.enqueue(paths, file, force=args.force)
    result = learner.drain(paths, cfg, judge=judge, wait_s=wait_s)
    mine = [row for row in result["processed"] if row.get("file") == str(file.resolve())]
    if mine:
        return _report_one(mine[-1], args)
    key = learner.queue_key(file)
    if any(entry.name.startswith(key) for entry in learner.pending(paths)):
        summary = {
            "session": file.stem,
            "file": str(file),
            "status": "queued",
            "reason": "another run holds the lock; this request is queued and the next run learns from it",
        }
    else:
        summary = {
            "session": file.stem,
            "file": str(file),
            "status": "taken",
            "reason": "another run picked up this request; rsi log shows its result",
        }
    return _report_one(summary, args)


def _report_one(summary: dict[str, Any], args: argparse.Namespace) -> int:
    if args.json:
        _json(summary)
    else:
        print(f"{summary.get('status')}: {summary.get('reason', '')}".strip(": "))
        for key in ("skill", "facts_added", "facts_retired", "refused", "dropped"):
            value = summary.get(key)
            if value:
                print(f"  {key}: {value}")
    return EXIT_OK if summary.get("status") not in ("error", "locked") else EXIT_STATE


def _report_drain(result: dict[str, Any], args: argparse.Namespace) -> int:
    if args.json:
        _json(result)
        return EXIT_OK
    for row in result["processed"]:
        print(f"{row.get('status')}: {row.get('session', '')} {row.get('reason', '')}".rstrip())
    if result["status"] == "waiting":
        print(f"another run holds the lock; {result['pending']} request(s) stay queued for it")
    elif not result["processed"]:
        print("queue empty")
    return EXIT_OK


def _facts(args: argparse.Namespace, paths: Paths) -> int:
    entries = (
        facts.load_all(paths)
        if args.all
        else [fact for fact in facts.load_all(paths) if fact.status == facts.STATUS_ACTIVE]
    )
    if args.json:
        _json({"facts": [fact.as_line() for fact in entries]})
        return EXIT_OK
    if not entries:
        print("no facts yet")
        return EXIT_OK
    for fact in sorted(entries, key=lambda f: (f.scope, f.created, f.id)):
        mark = "" if fact.status == facts.STATUS_ACTIVE else f" [{fact.status}: {fact.reason}]"
        print(f"{fact.scope:<12} {fact.id} ({fact.created}) {fact.text}{mark}")
    return EXIT_OK


def _forget(args: argparse.Namespace, paths: Paths) -> int:
    fact = facts.retire(paths, args.id, args.reason)
    print(f"retired {fact.id} ({fact.scope}): {fact.reason}")
    return EXIT_OK


def _skills(args: argparse.Namespace, paths: Paths) -> int:
    learned = skills.list_learned(paths)
    if args.json:
        _json(
            {
                "skills": [
                    {
                        "name": skill.name,
                        "scope": skill.scope,
                        "description": skill.description,
                        "path": str(skill.path),
                    }
                    for skill in learned
                ]
            }
        )
        return EXIT_OK
    if not learned:
        print("no learned skills yet")
        return EXIT_OK
    for skill in learned:
        print(f"{skill.scope:<12} {skill.name}: {skill.description}")
        print(f"{'':<12} {skill.path}")
    return EXIT_OK


def _log(args: argparse.Namespace, paths: Paths) -> int:
    rows = read_jsonl(paths.log)[-args.n :]
    if args.json:
        _json({"runs": rows})
        return EXIT_OK
    if not rows:
        print("no learning runs yet")
        return EXIT_OK
    for row in rows:
        print(
            f"{row.get('ts', '?')}  {row.get('status', '?'):<16} {row.get('session', '?')}"
            f"  project={row.get('project') or '-'}  {row.get('reason', '')}"
        )
    return EXIT_OK


def _stats(args: argparse.Namespace, paths: Paths) -> int:
    cfg = load(paths)
    payload: dict[str, Any] = {"home": str(paths.home)}
    if paths.db.exists():
        con = store.connect(paths.db, readonly=True)
        try:
            payload["index"] = store.stats(con)
        finally:
            con.close()
    else:
        payload["index"] = {"sessions": 0, "turns": 0, "projects": []}
    entries = facts.load_all(paths)
    payload["facts"] = {
        "active": sum(1 for fact in entries if fact.status == facts.STATUS_ACTIVE),
        "retired": sum(1 for fact in entries if fact.status != facts.STATUS_ACTIVE),
        "global": sum(1 for fact in entries if fact.scope == "global" and fact.status == "active"),
        "projects": sorted({fact.scope for fact in entries if fact.scope != "global"}),
    }
    payload["skills"] = len(skills.list_learned(paths))
    runs = read_jsonl(paths.log)
    statuses: dict[str, int] = {}
    for row in runs:
        status = str(row.get("status") or "?")
        statuses[status] = statuses.get(status, 0) + 1
    payload["learning"] = {
        "runs": len(runs),
        "by_status": statuses,
        "generator": cfg.generator.provider,
        "judge": cfg.judge.provider,
    }
    payload["projects"] = list(projects.load(paths))
    if args.json:
        _json(payload)
    else:
        print(f"home        {payload['home']}")
        print(
            f"index       {payload['index']['sessions']} sessions, "
            f"{payload['index']['turns']} turns"
        )
        print(
            f"facts       {payload['facts']['active']} active "
            f"({payload['facts']['global']} global, {payload['facts']['retired']} retired)"
        )
        print(f"skills      {payload['skills']} learned")
        print(
            f"learning    {payload['learning']['runs']} runs via "
            f"{payload['learning']['generator']} + {payload['learning']['judge']}"
        )
        for status, count in sorted(statuses.items()):
            print(f"            {status:<16} {count}")
    return EXIT_OK


def _json(payload: dict[str, Any]) -> None:
    import json

    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _fail(message: str, code: int) -> int:
    print(f"rsi: {message}", file=sys.stderr)
    return code


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        encoding = getattr(stream, "encoding", "") or ""
        if encoding.lower().replace("-", "") not in ("utf8", "utf8mb4"):
            with contextlib.suppress(AttributeError, ValueError):  # pragma: no cover
                stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
