"""Indexing and retrieval."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import guard, projects, session, store
from .config import Config
from .paths import Paths


@dataclass(slots=True)
class IndexResult:
    files: int = 0
    indexed: int = 0
    skipped: int = 0
    turns: int = 0
    errors: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "indexed": self.indexed,
            "skipped": self.skipped,
            "turns": self.turns,
            "errors": self.errors or [],
        }


def project_for(
    paths: Paths, cwd: str | Path | None, *, mapping: dict[str, list[Path]] | None = None
) -> str | None:
    if mapping is None:
        mapping = projects.load(paths)
    return projects.resolve(cwd, mapping=mapping)


def index_file(
    con: sqlite3.Connection,
    path: Path,
    *,
    paths: Paths,
    cfg: Config,
    mapping: dict[str, list[Path]] | None = None,
    force: bool = False,
) -> int:
    """Index one session file; returns the number of turns written."""
    parsed = session.parse(
        path,
        max_chars=cfg.retrieval.max_turn_chars,
        rules=guard.load_rules(paths.home / "guard.json"),
    )
    if parsed.skipped:
        return 0
    if not force:
        existing = store.session_fingerprint(con, parsed.record.id)
        if existing is not None:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC).replace(microsecond=0)
            indexed_at = existing.split("|", 1)[0]
            try:
                indexed = datetime.fromisoformat(indexed_at.replace("Z", "+00:00"))
            except ValueError:
                indexed = None
            if indexed is not None and indexed >= mtime:
                return 0
    parsed.record.project = project_for(paths, parsed.record.cwd, mapping=mapping)
    return store.index_session(con, parsed.record, parsed.turns)


def index_all(
    con: sqlite3.Connection,
    *,
    paths: Paths,
    cfg: Config,
    directory: Path | None = None,
    force: bool = False,
) -> IndexResult:
    root = (directory or cfg.sessions_dir).expanduser()
    result = IndexResult(errors=[])
    if not root.is_dir():
        result.errors.append(f"sessions directory does not exist: {root}")
        return result
    mapping = projects.load(paths)
    for path in sorted(root.rglob("*.jsonl")):
        result.files += 1
        try:
            written = index_file(con, path, paths=paths, cfg=cfg, mapping=mapping, force=force)
        except (OSError, sqlite3.Error) as exc:
            result.errors.append(f"{path}: {exc}")
            continue
        if written:
            result.indexed += 1
            result.turns += written
        else:
            result.skipped += 1
    return result


def recall(
    con: sqlite3.Connection,
    query: str,
    *,
    paths: Paths,
    cfg: Config,
    cwd: str | Path | None = None,
    project: str | None = None,
    limit: int | None = None,
    recent: int | None = None,
    sort: str = "relevance",
    cross_project: bool = False,
) -> list[store.Hit]:
    if project is None and not cross_project:
        project = project_for(paths, cwd)
    return store.search(
        con,
        query,
        project=project,
        limit=limit if limit is not None else cfg.retrieval.candidates,
        recent=recent if recent is not None else cfg.retrieval.recent,
        sort=sort,
    )
