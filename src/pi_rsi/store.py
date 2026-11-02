"""Recall storage: SQLite with FTS5 and the trigram tokenizer.

One implementation, on purpose. Substring matching with trigrams covers Chinese and
English without a tokenizer or an embedding model, and the standard library ships the
database. The module is the only place that knows SQL; the CLI, the learner and the
``recall`` tool talk to it through functions.

Schema changes bump :data:`SCHEMA_VERSION` and are applied by :func:`migrate`, which is
idempotent and runs on every connection. ``rsi reindex`` rebuilds the index from the
session files after a change to cleaning or redaction rules.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

TOKEN_RE = re.compile(r"[\w\u3400-\u9fff][\w\u3400-\u9fff.+#'/-]*", re.UNICODE)
MIN_TOKEN = 3  # trigram tokenizer cannot match anything shorter

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    harness     TEXT NOT NULL,
    project     TEXT,
    cwd         TEXT,
    source      TEXT NOT NULL,
    started_ts  TEXT,
    indexed_ts  TEXT NOT NULL,
    tool_calls  INTEGER NOT NULL DEFAULT 0,
    user_turns  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS turns (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    session  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn     INTEGER NOT NULL,
    ts       TEXT,
    kind     TEXT NOT NULL,
    text     TEXT NOT NULL,
    chars    INTEGER NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
    text,
    content = 'turns',
    content_rowid = 'id',
    tokenize = 'trigram'
);

CREATE TRIGGER IF NOT EXISTS turns_ai AFTER INSERT ON turns BEGIN
    INSERT INTO turns_fts (rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS turns_ad AFTER DELETE ON turns BEGIN
    INSERT INTO turns_fts (turns_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

CREATE TRIGGER IF NOT EXISTS turns_au AFTER UPDATE ON turns BEGIN
    INSERT INTO turns_fts (turns_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO turns_fts (rowid, text) VALUES (new.id, new.text);
END;

CREATE INDEX IF NOT EXISTS turns_session ON turns (session, turn);
CREATE INDEX IF NOT EXISTS turns_ts ON turns (ts);
CREATE INDEX IF NOT EXISTS sessions_project ON sessions (project);
"""


class StoreError(Exception):
    pass


@dataclass(slots=True)
class Turn:
    turn: int
    ts: str | None
    kind: str
    text: str


@dataclass(slots=True)
class SessionRecord:
    id: str
    harness: str
    project: str | None
    cwd: str | None
    source: str
    started_ts: str | None
    tool_calls: int = 0
    user_turns: int = 0


@dataclass(slots=True)
class Hit:
    session: str
    harness: str
    project: str | None
    turn: int
    ts: str | None
    kind: str
    text: str
    relevance: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "session": self.session,
            "harness": self.harness,
            "project": self.project,
            "turn": self.turn,
            "ts": self.ts,
            "kind": self.kind,
            "relevance": round(self.relevance, 4),
            "text": self.text,
        }


def now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if readonly:
        uri = f"file:{path}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=10.0)
    else:
        con = sqlite3.connect(path, timeout=10.0)
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.row_factory = sqlite3.Row
    if not readonly:
        migrate(con)
    return con


def migrate(con: sqlite3.Connection) -> int:
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise StoreError(
            f"database schema v{version} is newer than this pi-rsi (v{SCHEMA_VERSION}); upgrade the package"
        )
    if version == SCHEMA_VERSION:
        _require_fts5(con)
        return version
    with con:
        con.executescript(SCHEMA)
        con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    _require_fts5(con)
    return SCHEMA_VERSION


def _require_fts5(con: sqlite3.Connection) -> None:
    try:
        con.execute("SELECT count(*) FROM turns_fts").fetchone()
    except sqlite3.OperationalError as exc:  # pragma: no cover - depends on the build
        raise StoreError(
            "this Python's SQLite lacks FTS5 or the trigram tokenizer "
            f"(sqlite {sqlite3.sqlite_version}): {exc}"
        ) from exc


def index_session(
    con: sqlite3.Connection,
    record: SessionRecord,
    turns: Iterable[Turn],
    *,
    indexed_at: str | None = None,
) -> int:
    """Replace one session's rows. Idempotent: re-indexing a file is safe."""
    rows = list(turns)
    with con:
        con.execute(
            """
            INSERT INTO sessions (id, harness, project, cwd, source, started_ts, indexed_ts,
                                  tool_calls, user_turns)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                harness=excluded.harness, project=excluded.project, cwd=excluded.cwd,
                source=excluded.source, started_ts=excluded.started_ts,
                indexed_ts=excluded.indexed_ts, tool_calls=excluded.tool_calls,
                user_turns=excluded.user_turns
            """,
            (
                record.id,
                record.harness,
                record.project,
                record.cwd,
                record.source,
                record.started_ts,
                indexed_at or now(),
                record.tool_calls,
                record.user_turns,
            ),
        )
        con.execute("DELETE FROM turns WHERE session = ?", (record.id,))
        con.executemany(
            "INSERT INTO turns (session, turn, ts, kind, text, chars) VALUES (?, ?, ?, ?, ?, ?)",
            [(record.id, t.turn, t.ts, t.kind, t.text, len(t.text)) for t in rows],
        )
    return len(rows)


def session_fingerprint(con: sqlite3.Connection, session_id: str) -> str | None:
    row = con.execute(
        "SELECT indexed_ts, tool_calls, user_turns FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return None
    return f"{row['indexed_ts']}|{row['tool_calls']}|{row['user_turns']}"


def search(
    con: sqlite3.Connection,
    query: str,
    *,
    project: str | None = None,
    limit: int = 20,
    recent: int = 8,
    sort: str = "relevance",
) -> list[Hit]:
    """Keyword candidates plus the newest matching turns.

    ``bm25`` favours long, old turns, while questions like "what did we conclude last
    time" need the newest ones, so both sets are merged. Ranking is finished by the
    caller (or by the judge when one is configured).
    """
    expr = fts_query(query)
    if expr is None:
        return _like_search(con, query, project=project, limit=limit, recent=recent, sort=sort)

    project_clause = "AND s.project IS ?" if project is not None else ""
    params: list[Any] = [expr]
    if project is not None:
        params.append(project)

    relevance_rows = con.execute(
        f"""
        SELECT t.session, s.harness, s.project, t.turn, t.ts, t.kind, t.text,
               -bm25(turns_fts) AS score
        FROM turns_fts
        JOIN turns t ON t.id = turns_fts.rowid
        JOIN sessions s ON s.id = t.session
        WHERE turns_fts MATCH ? {project_clause}
        ORDER BY score DESC
        LIMIT ?
        """,
        [*params, max(1, limit)],
    ).fetchall()

    recent_rows = con.execute(
        f"""
        SELECT t.session, s.harness, s.project, t.turn, t.ts, t.kind, t.text,
               -bm25(turns_fts) AS score
        FROM turns_fts
        JOIN turns t ON t.id = turns_fts.rowid
        JOIN sessions s ON s.id = t.session
        WHERE turns_fts MATCH ? {project_clause}
        ORDER BY t.ts DESC
        LIMIT ?
        """,
        [*params, max(0, recent)],
    ).fetchall()

    hits: dict[tuple[str, int], Hit] = {}
    for row in [*relevance_rows, *recent_rows]:
        hit = _hit(row)
        hits.setdefault((hit.session, hit.turn), hit)

    ordered = list(hits.values())
    if sort == "recent":
        ordered.sort(key=lambda h: (h.ts or "", h.relevance), reverse=True)
    else:
        ordered.sort(key=lambda h: h.relevance, reverse=True)
    return ordered


def _like_search(
    con: sqlite3.Connection,
    query: str,
    *,
    project: str | None,
    limit: int,
    recent: int,
    sort: str,
) -> list[Hit]:
    """Fallback for queries shorter than three characters, where trigram cannot match."""
    needle = (query or "").strip()
    if not needle:
        return []
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    project_clause = "AND s.project IS ?" if project is not None else ""
    params: list[Any] = [f"%{escaped}%"]
    if project is not None:
        params.append(project)
    rows = con.execute(
        f"""
        SELECT t.session, s.harness, s.project, t.turn, t.ts, t.kind, t.text, 0.0 AS score
        FROM turns t JOIN sessions s ON s.id = t.session
        WHERE t.text LIKE ? ESCAPE '\\' {project_clause}
        ORDER BY t.ts DESC
        LIMIT ?
        """,
        [*params, max(1, limit + recent)],
    ).fetchall()
    hits = [_hit(row) for row in rows]
    if sort != "recent":
        hits.sort(key=lambda h: len(h.text))
    return hits


def _hit(row: sqlite3.Row) -> Hit:
    return Hit(
        session=row["session"],
        harness=row["harness"],
        project=row["project"],
        turn=row["turn"],
        ts=row["ts"],
        kind=row["kind"],
        text=row["text"],
        relevance=float(row["score"]),
    )


def fts_query(query: str, *, max_tokens: int = 12) -> str | None:
    """Build a quoted OR-expression of trigram-friendly tokens, or ``None``."""
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in TOKEN_RE.findall(query or ""):
        token = raw.strip(".-/#'")
        if len(token) < MIN_TOKEN:
            continue
        key = token.casefold()
        if key in seen:
            continue
        seen.add(key)
        tokens.append(token)
        if len(tokens) >= max_tokens:
            break
    if not tokens:
        return None
    return " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)


def stats(con: sqlite3.Connection) -> dict[str, Any]:
    sessions = con.execute("SELECT count(*) FROM sessions").fetchone()[0]
    turns = con.execute("SELECT count(*) FROM turns").fetchone()[0]
    by_project = [
        {"project": row[0], "sessions": row[1], "turns": row[2]}
        for row in con.execute(
            """
            SELECT s.project, count(DISTINCT s.id), count(t.id)
            FROM sessions s LEFT JOIN turns t ON t.session = s.id
            GROUP BY s.project ORDER BY count(t.id) DESC
            """
        )
    ]
    return {
        "schema_version": con.execute("PRAGMA user_version").fetchone()[0],
        "sessions": sessions,
        "turns": turns,
        "projects": by_project,
    }


def drop_session(con: sqlite3.Connection, session_id: str) -> bool:
    with con:
        cur = con.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    return cur.rowcount > 0


def rebuild(con: sqlite3.Connection) -> None:
    with con:
        con.execute("DELETE FROM sessions")
        con.execute("INSERT INTO turns_fts (turns_fts) VALUES ('rebuild')")
