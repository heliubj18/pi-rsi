"""``rsi doctor``: say why the loop will or will not work here.

Every check is something that has actually prevented learning from running: a Python
without FTS5, a home directory on a read-only mount, a configured judge whose key is
missing, a sessions directory that does not exist yet because pi has not been used.

Exit status is 1 when a check fails, 0 when everything is at least usable. Warnings are
conditions that degrade quality, not availability.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import facts, judges, projects, skills, store, util
from .config import Config, ConfigError, load, resolve_secret
from .paths import Paths

OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass(slots=True)
class Check:
    name: str
    status: str
    detail: str
    hint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "detail": self.detail, "hint": self.hint}


def run(paths: Paths, *, probe: bool = False) -> list[Check]:
    checks = [
        _python(),
        _sqlite(),
        _home(paths),
        _config(paths),
        _projects(paths),
        _sessions(paths),
    ]
    try:
        cfg = load(paths)
    except ConfigError as exc:
        checks.append(Check("configuration", FAIL, str(exc), "fix config.toml"))
        return checks
    checks.append(_generator(cfg))
    checks.append(_judge(cfg, probe=probe))
    checks.extend(_state(paths, cfg))
    return checks


def _python() -> Check:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    return Check("python", OK, version)


def _sqlite() -> Check:
    try:
        with tempfile.TemporaryDirectory() as directory:
            con = sqlite3.connect(Path(directory) / "probe.db")
            try:
                con.execute("CREATE VIRTUAL TABLE probe USING fts5(text, tokenize='trigram')")
                con.execute("INSERT INTO probe VALUES ('trigram tokenizer works')")
                con.execute("SELECT rowid FROM probe WHERE probe MATCH '\"tokenizer\"'").fetchone()
            finally:
                con.close()
    except sqlite3.Error as exc:
        return Check(
            "sqlite",
            FAIL,
            f"{sqlite3.sqlite_version}: {exc}",
            "install a Python built against SQLite 3.34 or newer with FTS5",
        )
    return Check("sqlite", OK, f"{sqlite3.sqlite_version} with FTS5 trigram")


def _home(paths: Paths) -> Check:
    home = paths.home
    try:
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        return Check("home", FAIL, f"{home}: {exc}", "set RSI_HOME to a writable directory")
    if not os.access(home, os.W_OK):
        return Check(
            "home", FAIL, f"{home} is not writable", "set RSI_HOME to a writable directory"
        )
    mode = home.stat().st_mode & 0o777
    if mode & 0o077:
        return Check(
            "home",
            WARN,
            f"{home} is mode {mode:o}; learned text can contain project details",
            f"chmod 700 {home}",
        )
    return Check("home", OK, str(home))


def _config(paths: Paths) -> Check:
    if not paths.config.exists():
        return Check(
            "config",
            WARN,
            "no config.toml; using the defaults (pi generator, no judge)",
            f"run `rsi init` to write {paths.config}",
        )
    try:
        load(paths)
    except ConfigError as exc:
        return Check("config", FAIL, str(exc), f"fix {paths.config}")
    return Check("config", OK, str(paths.config))


def _projects(paths: Paths) -> Check:
    try:
        mapping = projects.load(paths)
    except ConfigError as exc:
        return Check("projects", FAIL, str(exc), f"fix {paths.projects_file}")
    if not mapping:
        return Check(
            "projects",
            WARN,
            f"no mappings in {paths.projects_file}; everything will be global",
            "map a directory to a project so facts and skills stay scoped",
        )
    missing = [str(d) for dirs in mapping.values() for d in dirs if not d.exists()]
    detail = f"{len(mapping)} projects"
    if missing:
        return Check("projects", WARN, f"{detail}; missing: {', '.join(missing[:3])}")
    return Check("projects", OK, detail)


def _sessions(paths: Paths) -> Check:
    try:
        cfg = load(paths)
    except ConfigError:
        return Check("sessions", WARN, "skipped: configuration is invalid")
    directory = cfg.sessions_dir
    if not directory.is_dir():
        return Check(
            "sessions",
            WARN,
            f"{directory} does not exist",
            "run pi once, or set [sessions] dir in config.toml",
        )
    count = sum(1 for _ in directory.rglob("*.jsonl"))
    if count == 0:
        return Check("sessions", WARN, f"{directory} has no sessions yet")
    return Check("sessions", OK, f"{count} files in {directory}")


def _generator(cfg: Config) -> Check:
    provider = cfg.generator.provider
    if provider == "pi":
        binary = shutil.which(os.environ.get("RSI_PI_BIN", "pi"))
        if binary is None:
            return Check(
                "generator",
                FAIL,
                "provider pi, but no 'pi' executable on PATH",
                "install pi or configure an openai/anthropic generator",
            )
        return Check("generator", OK, f"pi ({binary}), model {cfg.generator.model or 'default'}")
    try:
        resolve_secret(cfg.generator.api_key)
    except ConfigError as exc:
        return Check("generator", FAIL, f"{provider}: {exc}", "check generator.api_key")
    return Check(
        "generator",
        OK,
        f"{provider} {cfg.generator.model or '(no model)'} at {cfg.generator.base_url}",
    )


def _judge(cfg: Config, *, probe: bool) -> Check:
    if cfg.judge.provider == "none":
        return Check(
            "judge",
            WARN,
            "no judge: every eligible session costs one generator call",
            "configure [judge] to gate and review what is learned",
        )
    try:
        resolve_secret(cfg.judge.api_key)
    except ConfigError as exc:
        return Check("judge", FAIL, f"{cfg.judge.provider}: {exc}", "check judge.api_key")
    if not probe:
        return Check(
            "judge",
            OK,
            f"{cfg.judge.provider} {cfg.judge.model or '(no model)'} at {cfg.judge.base_url}",
        )
    judge = judges.build(cfg.judge)
    assert judge is not None
    try:
        answers = judge.ask({"probe": judges.noul("One plus one equals two.")})
    except judges.JudgeError as exc:
        return Check("judge", FAIL, f"probe failed: {exc}", "check the endpoint, model and key")
    if judges.probability(answers, "probe") < 0.5:
        return Check(
            "judge",
            WARN,
            f"probe answered {judges.probability(answers, 'probe'):.2f} to a statement that is true",
            "the backend may be ignoring the questions; do not rely on its answers",
        )
    return Check("judge", OK, f"{cfg.judge.provider} answered the probe")


def _state(paths: Paths, cfg: Config) -> list[Check]:
    checks: list[Check] = []
    learned = skills.list_learned(paths)
    entries = facts.load_all(paths)
    active = facts.active(entries)
    checks.append(
        Check(
            "state",
            OK,
            f"{len(active)} active facts, {len(learned)} learned skills",
        )
    )
    if paths.db.exists():
        try:
            con = store.connect(paths.db, readonly=True)
            try:
                stats = store.stats(con)
            finally:
                con.close()
            checks.append(
                Check(
                    "recall",
                    OK,
                    f"{stats['sessions']} sessions, {stats['turns']} turns, schema v{stats['schema_version']}",
                )
            )
        except (sqlite3.Error, store.StoreError) as exc:
            checks.append(Check("recall", FAIL, str(exc), "run `rsi reindex --force`"))
    else:
        checks.append(Check("recall", WARN, "no recall database yet", "run `rsi index --all`"))
    try:
        with util.locked(paths.lock):
            pass
        checks.append(Check("lock", OK, "not held"))
    except util.LockedError:
        checks.append(
            Check("lock", WARN, f"{paths.lock} is held by another run", "wait for it to finish")
        )
    if learned and not paths.skills_for("global").exists():
        checks.append(Check("skills", WARN, "no global skills directory yet"))
    if cfg.learning.max_facts_per_scope <= 0:
        checks.append(Check("limits", WARN, "max_facts_per_scope is 0: no facts can be written"))
    return checks


def report(checks: list[Check]) -> str:
    lines: list[str] = []
    for check in checks:
        lines.append(f"{check.status:<4} {check.name:<12} {check.detail}")
        if check.hint and check.status != OK:
            lines.append(f"     {'':<12} -> {check.hint}")
    return "\n".join(lines)


def failed(checks: list[Check]) -> bool:
    return any(check.status == FAIL for check in checks)
