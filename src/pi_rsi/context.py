"""What gets injected into the system prompt.

Facts are cheap and always relevant enough to carry: they are what was learned about
*this* project or about the tools. Skills are not injected; pi loads them from the
directory the extension publishes and the model opens them on demand.

The block is bounded and stable. Stability matters as much as content: an injected
section that changes every turn breaks prompt caching, so the learned-facts block is
rewritten only when a fact is added or retired.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from . import episodes, facts, projects, skills, store
from .config import Config
from .paths import Paths


def build(
    paths: Paths,
    cfg: Config,
    *,
    cwd: str | Path | None = None,
    mapping: dict[str, list[Path]] | None = None,
) -> dict[str, Any]:
    mapping = projects.load(paths) if mapping is None else mapping
    project = projects.resolve(cwd, mapping=mapping)
    entries = facts.visible(paths, project)
    learned = skills.list_learned(paths, scope=None)
    if project:
        learned = [skill for skill in learned if skill.scope in (project, "global")]
    else:
        learned = [skill for skill in learned if skill.scope == "global"]
    return {
        "home": str(paths.home),
        "cwd": str(cwd) if cwd else None,
        "project": project,
        "facts": facts.render(entries, limit_chars=cfg.retrieval.max_inject_chars),
        "fact_count": len(entries),
        "skills": [{"name": skill.name, "scope": skill.scope} for skill in learned],
        "skill_paths": skill_paths(paths, project),
        "sessions_indexed": _sessions_indexed(paths),
    }


def skill_paths(paths: Paths, project: str | None) -> list[str]:
    """Directories the extension hands to pi through ``resources_discover``."""
    directories = [paths.learned_skills]
    if project:
        directories.append(paths.skills_for(project))
    return [
        str(directory)
        for directory in directories
        if directory.is_dir() and any(directory.glob("*/SKILL.md"))
    ]


def render_text(context: dict[str, Any]) -> str:
    lines: list[str] = []
    if context.get("project"):
        lines.append(f"Project: {context['project']}")
    if context.get("facts"):
        lines.append(context["facts"])
    if context.get("skill_count"):
        lines.append(f"{context['skill_count']} learned skills are installed.")
    return "\n\n".join(lines)


def _sessions_indexed(paths: Paths) -> int:
    if not paths.db.exists():
        return 0
    try:
        con = store.connect(paths.db, readonly=True)
    except (sqlite3.Error, store.StoreError):
        return 0
    try:
        row = con.execute("SELECT count(*) FROM sessions").fetchone()
    except sqlite3.Error:
        return 0
    finally:
        con.close()
    return int(row[0]) if row else 0


def project_for(paths: Paths, cwd: str | Path | None) -> str | None:
    return episodes.project_for(paths, cwd)
