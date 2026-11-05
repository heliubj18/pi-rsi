"""Filesystem layout.

Everything the agent learns is mutable state and lives outside the package, under
``$RSI_HOME`` (default: ``rsi`` next to pi's own agent directory, so ``~/.pi/rsi``).
The package itself is read-only: ``pi install`` reconciles the checkout, so writing
learned material into it would be lost on update.

    $RSI_HOME/
        config.toml            user configuration (endpoints, models, thresholds)
        projects.json          directory -> project name, optional
        facts.jsonl            global facts
        projects/<name>/       per-project facts and skills
        skills/learned/<name>/ learner-written skills (this is what the model sees)
        state/                 database, logs, snapshots, lock
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

ENV_HOME = "RSI_HOME"
ENV_PI_AGENT_DIR = "PI_CODING_AGENT_DIR"
ENV_PI_SESSION_DIR = "PI_CODING_AGENT_SESSION_DIR"

GLOBAL_SCOPE = "global"


def pi_agent_dir() -> Path:
    """pi's configuration directory, resolved the way pi resolves it."""
    raw = os.environ.get(ENV_PI_AGENT_DIR, "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".pi" / "agent"


def default_home() -> Path:
    """Sibling of pi's agent directory: a relocated pi home carries rsi with it."""
    return pi_agent_dir().parent / "rsi"


def default_sessions_dir() -> Path:
    """Where pi stores sessions, following pi's own precedence.

    ``--session-dir`` is not visible here, so an explicit ``[sessions] dir`` or
    ``RSI_SESSIONS_DIR`` is what covers a one-off flag: this is the default only.
    """
    raw = os.environ.get(ENV_PI_SESSION_DIR, "").strip()
    if raw:
        return Path(raw).expanduser()
    settings = pi_agent_dir() / "settings.json"
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return pi_agent_dir() / "sessions"
    configured = data.get("sessionDir") if isinstance(data, dict) else None
    if isinstance(configured, str) and configured.strip():
        candidate = Path(configured.strip()).expanduser()
        # A relative sessionDir resolves from pi's working directory, which is not
        # known here; ignoring it is better than resolving it against the wrong root.
        if candidate.is_absolute():
            return candidate
    return pi_agent_dir() / "sessions"


@dataclass(frozen=True)
class Paths:
    home: Path

    @classmethod
    def resolve(cls, home: str | Path | None = None) -> Paths:
        """Resolve ``$RSI_HOME``; never creates anything."""
        raw = home if home is not None else os.environ.get(ENV_HOME)
        root = Path(raw).expanduser() if raw else default_home()
        return cls(root.resolve())

    @property
    def config(self) -> Path:
        return self.home / "config.toml"

    @property
    def projects_file(self) -> Path:
        return self.home / "projects.json"

    @property
    def facts_file(self) -> Path:
        return self.home / "facts.jsonl"

    @property
    def skills(self) -> Path:
        return self.home / "skills"

    @property
    def learned_skills(self) -> Path:
        return self.skills / "learned"

    @property
    def projects(self) -> Path:
        return self.home / "projects"

    @property
    def state(self) -> Path:
        return self.home / "state"

    @property
    def db(self) -> Path:
        return self.state / "recall.sqlite3"

    @property
    def log(self) -> Path:
        return self.state / "learn.jsonl"

    @property
    def learn_out(self) -> Path:
        return self.state / "learn.out"

    @property
    def queue(self) -> Path:
        """Pending learning requests, one file per session (see ``learner.enqueue``)."""
        return self.state / "queue"

    @property
    def snapshots(self) -> Path:
        return self.state / "snapshots"

    @property
    def lock(self) -> Path:
        return self.state / "learn.lock"

    def project_dir(self, scope: str) -> Path:
        if scope == GLOBAL_SCOPE:
            raise ValueError("global scope has no project directory")
        if not scope or "/" in scope or scope.startswith("."):
            raise ValueError(f"invalid project name: {scope!r}")
        return self.projects / scope

    def facts_for(self, scope: str) -> Path:
        return self.facts_file if scope == GLOBAL_SCOPE else self.project_dir(scope) / "facts.jsonl"

    def skills_for(self, scope: str) -> Path:
        return self.learned_skills if scope == GLOBAL_SCOPE else self.project_dir(scope) / "skills"

    def project_facts_files(self) -> list[Path]:
        if not self.projects.is_dir():
            return []
        return sorted(self.projects.glob("*/facts.jsonl"))

    def project_skill_dirs(self) -> list[tuple[str, Path]]:
        if not self.projects.is_dir():
            return []
        return [(d.name, d / "skills") for d in sorted(self.projects.iterdir()) if d.is_dir()]

    def ensure(self) -> None:
        """Create the home tree with owner-only permissions."""
        for directory in (
            self.home,
            self.state,
            self.snapshots,
            self.learned_skills,
            self.projects,
        ):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
