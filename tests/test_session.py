from __future__ import annotations

import json
from pathlib import Path

from conftest import make_session
from pi_rsi import session


def test_parses_metadata_and_counts(tmp_path: Path) -> None:
    path = make_session(tmp_path / "s.jsonl", cwd="/tmp/proj", pairs=[("q1", "a1"), ("q2", "a2")])
    parsed = session.parse(path)
    assert parsed.record.id == "s-0001"
    assert parsed.record.cwd == "/tmp/proj"
    assert parsed.record.user_turns == 2
    assert parsed.record.tool_calls == 6
    assert len(parsed.turns) == 2
    assert parsed.malformed_lines == 0


def test_answer_comes_before_steps(tmp_path: Path) -> None:
    path = make_session(tmp_path / "s.jsonl", pairs=[("why?", "Because the tag is stale.")])
    parsed = session.parse(path)
    text = parsed.turns[0].text
    assert text.startswith("[USER] why?")
    assert "[ANSWER] Because the tag is stale." in text
    assert text.index("[ANSWER]") < text.index("[tool bash]")


def test_redacts_before_indexing(tmp_path: Path) -> None:
    path = make_session(
        tmp_path / "s.jsonl",
        pairs=[("use sk-abcdefghijklmnopqrstuvwx", "ok")],
    )
    parsed = session.parse(path)
    assert "sk-abcdefghijklmnopqrstuvwx" not in parsed.turns[0].text
    assert session.guard.REDACTED in parsed.turns[0].text


def test_truncates_long_turns(tmp_path: Path) -> None:
    path = make_session(tmp_path / "s.jsonl", pairs=[("q", "x" * 5000)])
    parsed = session.parse(path, max_chars=200)
    assert len(parsed.turns[0].text) <= 200 + len(session.TRUNCATED)
    assert parsed.turns[0].text.endswith("…[truncated]")


def test_collects_feedback_entries(tmp_path: Path) -> None:
    path = make_session(
        tmp_path / "s.jsonl",
        custom=[
            {
                "type": "custom",
                "customType": "rsi/feedback",
                "data": {"rating": "down", "reason": "the answer was wrong"},
            },
            {"type": "custom", "customType": "other", "data": {"x": 1}},
        ],
    )
    parsed = session.parse(path)
    assert parsed.feedback == [{"rating": "down", "reason": "the answer was wrong"}]


def test_includes_compaction_summary(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    lines = [
        {"type": "session", "id": "s-9", "timestamp": "2026-01-01T00:00:00Z", "cwd": "/tmp/p"},
        {
            "type": "message",
            "message": {"role": "user", "content": [{"type": "text", "text": "q"}]},
        },
        {"type": "compaction", "summary": "we decided to pin the tag"},
        {
            "type": "message",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "a"}]},
        },
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    parsed = session.parse(path)
    assert "[SUMMARY] we decided to pin the tag" in parsed.turns[0].text


def test_malformed_lines_are_counted_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"type": "session", "id": "s-1", "cwd": "/tmp/p"}),
                "{not json",
                json.dumps({"type": "message", "message": {"role": "user", "content": "q"}}),
            ]
        ),
        encoding="utf-8",
    )
    parsed = session.parse(path)
    assert parsed.malformed_lines == 1
    assert parsed.record.user_turns == 1


def test_session_without_user_turns_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text(
        json.dumps({"type": "session", "id": "s-2", "cwd": "/tmp/p"}) + "\n",
        encoding="utf-8",
    )
    assert session.parse(path).skipped is True


def test_missing_cwd_is_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text(
        json.dumps({"type": "message", "message": {"role": "user", "content": "q"}}),
        encoding="utf-8",
    )
    parsed = session.parse(path)
    assert parsed.record.cwd is None
    assert parsed.record.id == "s"
