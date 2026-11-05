"""The learning queue: a run that finds the lock held must not lose its request."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import make_session
from pi_rsi import learner, util
from pi_rsi.config import load
from pi_rsi.paths import Paths

ROOT = Path(__file__).resolve().parents[1]


class NullGenerator:
    name = "null"

    def generate(self, system: str, prompt: str) -> str:
        return json.dumps({"reason": "nothing", "skill": None, "facts": {"add": []}})


def session(path: Path, session_id: str) -> Path:
    return make_session(path, session_id=session_id)


def requests(paths: Paths) -> list[dict]:
    return [json.loads(entry.read_text()) for entry in learner.pending(paths)]


def logged(paths: Paths) -> list[dict]:
    if not paths.log.exists():
        return []
    return [json.loads(line) for line in paths.log.read_text().splitlines()]


def test_enqueue_keeps_one_request_per_transcript(paths: Paths, tmp_path: Path) -> None:
    first = session(tmp_path / "a.jsonl", "a")
    learner.enqueue(paths, first)
    learner.enqueue(paths, first, force=True)
    learner.enqueue(paths, session(tmp_path / "b.jsonl", "b"), settle=True)
    by_file = {Path(r["file"]).stem: r for r in requests(paths)}
    assert set(by_file) == {"a", "b"}
    assert by_file["a"]["force"] is True and by_file["a"]["settle"] is False
    assert by_file["b"]["settle"] is True


def test_drain_learns_every_queued_session(paths: Paths, tmp_path: Path) -> None:
    for index in range(3):
        learner.enqueue(paths, session(tmp_path / f"s{index}.jsonl", f"s{index}"), force=True)
    result = learner.drain(paths, load(paths), generator=NullGenerator(), wait_s=0)
    assert result["status"] == "drained" and result["pending"] == 0
    assert sorted(row["session"] for row in result["processed"]) == ["s0", "s1", "s2"]
    assert learner.pending(paths) == []
    assert sorted(row["session"] for row in logged(paths)) == ["s0", "s1", "s2"]


def test_busy_lock_leaves_the_request_queued(paths: Paths, tmp_path: Path) -> None:
    learner.enqueue(paths, session(tmp_path / "late.jsonl", "late"))
    with util.locked(paths.lock):
        result = learner.drain(paths, load(paths), generator=NullGenerator(), wait_s=0)
    assert result["status"] == "waiting" and result["pending"] == 1
    assert logged(paths) == []  # nothing recorded as locked, nothing dropped
    later = learner.drain(paths, load(paths), generator=NullGenerator(), wait_s=0)
    assert [row["session"] for row in later["processed"]] == ["late"]


def test_request_queued_while_draining_is_processed_before_release(
    paths: Paths, tmp_path: Path
) -> None:
    second = session(tmp_path / "second.jsonl", "second")

    class EnqueueingGenerator(NullGenerator):
        calls = 0

        def generate(self, system: str, prompt: str) -> str:
            EnqueueingGenerator.calls += 1
            if EnqueueingGenerator.calls == 1:  # another session ends meanwhile
                learner.enqueue(paths, second, force=True)
            return super().generate(system, prompt)

    learner.enqueue(paths, session(tmp_path / "first.jsonl", "first"), force=True)
    result = learner.drain(paths, load(paths), generator=EnqueueingGenerator(), wait_s=0)
    assert [row["session"] for row in result["processed"]] == ["first", "second"]
    assert learner.pending(paths) == []


def test_drain_stops_at_its_limit(paths: Paths, tmp_path: Path) -> None:
    for index in range(3):
        learner.enqueue(paths, session(tmp_path / f"l{index}.jsonl", f"l{index}"), force=True)
    result = learner.drain(paths, load(paths), generator=NullGenerator(), wait_s=0, limit=2)
    assert result["status"] == "partial" and result["pending"] == 1
    assert len(result["processed"]) == 2


def test_claim_left_by_a_crashed_run_is_resumed(paths: Paths, tmp_path: Path) -> None:
    entry = learner.enqueue(paths, session(tmp_path / "crash.jsonl", "crash"), force=True)
    entry.rename(entry.with_name(entry.name.replace(learner.QUEUE_SUFFIX, learner.CLAIM_SUFFIX)))
    result = learner.drain(paths, load(paths), generator=NullGenerator(), wait_s=0)
    assert [row["session"] for row in result["processed"]] == ["crash"]
    assert learner.pending(paths) == []


def test_unreadable_request_is_dropped_not_retried_forever(paths: Paths) -> None:
    paths.queue.mkdir(parents=True, exist_ok=True)
    (paths.queue / "broken.json").write_text("{not json", encoding="utf-8")
    result = learner.drain(paths, load(paths), generator=NullGenerator(), wait_s=0)
    assert result["processed"][0]["status"] == "error"
    assert learner.pending(paths) == []


def test_locked_waits_then_gives_up(paths: Paths) -> None:
    with util.locked(paths.lock):
        start = time.monotonic()
        with pytest.raises(util.LockedError), util.locked(paths.lock, wait_s=0.3, step_s=0.05):
            pass
        assert time.monotonic() - start >= 0.3


def test_concurrent_runs_lose_nothing(paths: Paths, tmp_path: Path) -> None:
    """Several sessions end at once: each is learned from once, none is reported locked."""
    files = [session(tmp_path / f"c{index}.jsonl", f"c{index}") for index in range(4)]
    env = {
        **os.environ,
        "RSI_HOME": str(paths.home),
        "RSI_HARNESS": "pi",
        "RSI_JUDGE_PROVIDER": "none",
        # An endpoint nothing listens on: every generator call fails fast, which is
        # enough to show each request was taken and logged exactly once.
        "RSI_GENERATOR_PROVIDER": "openai",
        "RSI_GENERATOR_BASE_URL": "http://127.0.0.1:9",
    }
    runs = [
        subprocess.Popen(
            [sys.executable, "-I", str(ROOT / "bin" / "rsi"), "learn", str(file), "--force"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        for file in files
    ]
    for run in runs:
        run.wait(timeout=120)
    rows = logged(paths)
    assert "locked" not in {row["status"] for row in rows}
    assert sorted(row["session"] for row in rows) == ["c0", "c1", "c2", "c3"]
    assert learner.pending(paths) == []
