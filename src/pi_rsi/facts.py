"""Facts: small, dated, citable claims.

One JSON object per line, append-only in spirit: retiring a fact appends a new version
of that line with ``status = "retired"`` and the others are preserved as history in the
same file. Every fact carries an id so a user can say ``/forget f-1a2b3c4d`` and the
model can name what it is relying on.

Facts are grouped by scope: ``global`` lives in ``$RSI_HOME/facts.jsonl``, a project in
``$RSI_HOME/projects/<name>/facts.jsonl``. The renderer shows ids and dates, and is
bounded, because an unbounded fact list slowly turns into prompt noise.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import GLOBAL_SCOPE, Paths

FACT_ID_RE = re.compile(r"^f-[0-9a-f]{8}$")
STATUS_ACTIVE = "active"
STATUS_RETIRED = "retired"
DEDUPE_RATIO = 0.86
MAX_FACT_CHARS = 400


class FactError(Exception):
    pass


@dataclass(slots=True)
class Fact:
    id: str
    text: str
    scope: str
    created: str
    source: str = "learn"
    status: str = STATUS_ACTIVE
    reason: str = ""
    retired: str = ""

    def as_line(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value != ""}


def make_id(text: str) -> str:
    digest = hashlib.sha1(normalize(text).encode("utf-8")).hexdigest()
    return f"f-{digest[:8]}"


def normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def numbers(text: str) -> list[str]:
    """Digit runs in a fact: versions, ports and counts are the part that matters."""
    return re.findall(r"\d+", text)


def near_duplicate(text: str, other: str) -> bool:
    """True when two facts say the same thing, allowing for rephrasing.

    Different numbers in otherwise identical text are a different fact: ``3.12`` and
    ``3.14`` are one character apart and completely different instructions.
    """
    if numbers(text) != numbers(other):
        return False
    ratio = difflib.SequenceMatcher(None, normalize(text), normalize(other)).ratio()
    return ratio >= DEDUPE_RATIO


def today(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).date().isoformat()


def read_file(path: Path, scope: str) -> list[Fact]:
    facts: list[Fact] = []
    if not path.exists():
        return facts
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict) or not raw.get("id") or not raw.get("text"):
            continue
        facts.append(
            Fact(
                id=str(raw["id"]),
                text=str(raw["text"]),
                scope=str(raw.get("scope") or scope),
                created=str(raw.get("created") or ""),
                source=str(raw.get("source") or "learn"),
                status=str(raw.get("status") or STATUS_ACTIVE),
                reason=str(raw.get("reason") or ""),
                retired=str(raw.get("retired") or ""),
            )
        )
    return facts


def write_file(path: Path, facts: Iterable[Fact]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lines = [json.dumps(fact.as_line(), ensure_ascii=False, sort_keys=True) for fact in facts]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_all(paths: Paths) -> list[Fact]:
    facts = read_file(paths.facts_file, GLOBAL_SCOPE)
    for path in paths.project_facts_files():
        scope = path.parent.name
        facts.extend(read_file(path, scope))
    return facts


def active(facts: Iterable[Fact]) -> list[Fact]:
    return [fact for fact in facts if fact.status == STATUS_ACTIVE]


def visible(paths: Paths, project: str | None) -> list[Fact]:
    """Global facts plus the current project's."""
    facts = read_file(paths.facts_file, GLOBAL_SCOPE)
    if project:
        facts.extend(read_file(paths.facts_for(project), project))
    return active(facts)


def find(paths: Paths, fact_id: str) -> tuple[Path, list[Fact], Fact] | None:
    for path, scope in _files(paths):
        facts = read_file(path, scope)
        for fact in facts:
            if fact.id == fact_id:
                return path, facts, fact
    return None


def _files(paths: Paths) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = [(paths.facts_file, GLOBAL_SCOPE)]
    files.extend((path, path.parent.name) for path in paths.project_facts_files())
    return files


@dataclass(slots=True)
class AddResult:
    fact: Fact | None = None
    duplicate_of: str | None = None
    errors: list[str] = field(default_factory=list)


def add(
    paths: Paths,
    text: str,
    *,
    scope: str = GLOBAL_SCOPE,
    source: str = "learn",
    now: datetime | None = None,
    max_chars: int = MAX_FACT_CHARS,
) -> AddResult:
    text = " ".join(text.split())
    if not text:
        return AddResult(errors=["empty fact"])
    if len(text) > max_chars:
        return AddResult(errors=[f"fact is {len(text)} chars, limit is {max_chars}"])
    path = paths.facts_for(scope)
    facts = read_file(path, scope)
    for existing in facts:
        if existing.status != STATUS_ACTIVE:
            continue
        if near_duplicate(text, existing.text):
            return AddResult(fact=existing, duplicate_of=existing.id)
    fact = Fact(
        id=make_id(text),
        text=text,
        scope=scope,
        created=today(now),
        source=source,
    )
    if any(existing.id == fact.id for existing in facts):
        return AddResult(fact=next(f for f in facts if f.id == fact.id), duplicate_of=fact.id)
    facts.append(fact)
    write_file(path, facts)
    return AddResult(fact=fact)


def retire(paths: Paths, fact_id: str, reason: str, *, now: datetime | None = None) -> Fact:
    if not FACT_ID_RE.match(fact_id):
        raise FactError(f"invalid fact id: {fact_id!r}")
    found = find(paths, fact_id)
    if found is None:
        raise FactError(f"no such fact: {fact_id}")
    path, facts, fact = found
    if fact.status != STATUS_ACTIVE:
        return fact
    fact.status = STATUS_RETIRED
    fact.reason = " ".join(reason.split())[:300]
    fact.retired = today(now)
    write_file(path, facts)
    return fact


def counts_today(paths: Paths, scope: str, *, now: datetime | None = None) -> int:
    stamp = today(now)
    return sum(1 for fact in read_file(paths.facts_for(scope), scope) if fact.created == stamp)


def render(
    facts: Iterable[Fact],
    *,
    limit_chars: int = 3000,
    header: str = "Facts you recorded earlier",
) -> str:
    """Compact block for the system prompt: id, date, text."""
    rows = [f"- {fact.id} ({fact.created}) {fact.text}" for fact in active(facts)]
    if not rows:
        return ""
    kept: list[str] = []
    size = 0
    for row in rows:
        if kept and size + len(row) + 1 > limit_chars:
            dropped = len(rows) - len(kept)
            kept.append(f"- ({dropped} more facts not shown; use the recall tool)")
            break
        kept.append(row)
        size += len(row) + 1
    return "\n".join([header + ":", *kept])
