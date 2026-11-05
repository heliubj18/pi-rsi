"""Small shared helpers: timestamps, JSONL logs, an advisory file lock."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any


class LockedError(Exception):
    pass


def utcnow(now: datetime | None = None) -> datetime:
    return now or datetime.now(UTC)


def stamp(now: datetime | None = None) -> str:
    return utcnow(now).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
    os.chmod(path, 0o600)


@contextlib.contextmanager
def locked(
    path: Path, *, blocking: bool = False, wait_s: float = 0.0, step_s: float = 0.5
) -> Iterator[IO[str]]:
    """Exclusive advisory lock.

    Raises :class:`LockedError` when the lock is held: at once by default, or after
    retrying for ``wait_s`` seconds. ``blocking`` waits without a limit.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = path.open("a+", encoding="utf-8")
    deadline = time.monotonic() + wait_s
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise LockedError(f"{path} is locked by another run") from exc
                time.sleep(step_s)
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        yield handle
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def wait_until_settled(
    path: Path, *, quiet_s: float = 2.0, limit_s: float = 30.0, step_s: float = 0.5
) -> bool:
    """Wait until ``path`` has not been modified for ``quiet_s``; False on timeout.

    A harness may still be writing a transcript when its end-of-session hook fires.
    """
    deadline = time.monotonic() + limit_s
    while True:
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return False
        if age >= quiet_s:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(step_s, max(quiet_s - age, 0.05)))


def clip(text: str, limit: int, *, marker: str = "…") -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit].rstrip() + marker
