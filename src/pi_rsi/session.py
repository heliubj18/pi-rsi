"""Read pi session files.

A session is JSONL. Each entry has a ``type``; message entries carry
``message.role`` and a list of content blocks (``text``, ``thinking``, ``toolCall``).
Compaction and branch summaries carry ``summary``.

One indexed row is one *turn*: a user message plus everything up to the next user
message. The text is ordered question, then the last substantive answer, then the
remaining steps. Retrieval should surface conclusions, not transcripts, so the answer
comes before the tool chatter that produced it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import guard
from .store import SessionRecord, Turn

ANSWER_PREFIX = "[ANSWER] "
USER_PREFIX = "[USER] "
SUMMARY_PREFIX = "[SUMMARY] "
TRUNCATED = "\n…[truncated]"

TOOL_ARG_CHARS = 300
TOOL_RESULT_CHARS = 600
SUMMARY_CHARS = 1200
MAX_SEGMENTS = 40


@dataclass(slots=True)
class ParsedSession:
    record: SessionRecord
    turns: list[Turn] = field(default_factory=list)
    feedback: list[dict[str, Any]] = field(default_factory=list)
    malformed_lines: int = 0

    @property
    def skipped(self) -> bool:
        return self.record.user_turns == 0


class _TurnBuilder:
    def __init__(self, *, max_chars: int, rules: tuple[guard.Rule, ...], ts: str | None) -> None:
        self.max_chars = max_chars
        self.rules = rules
        self.ts = ts
        self.user = ""
        self.segments: list[str] = []
        self.answer = ""
        self.count = 0

    def add_user(self, text: str) -> None:
        self.user = text.strip()

    def add_answer(self, text: str) -> None:
        text = text.strip()
        if text:
            self.answer = text

    def add_segment(self, text: str) -> None:
        text = text.strip()
        if text and len(self.segments) < MAX_SEGMENTS:
            self.segments.append(text)

    def build(self, index: int, ts: str | None) -> Turn | None:
        if not self.user and not self.answer and not self.segments:
            return None
        parts = [USER_PREFIX + self.user if self.user else USER_PREFIX + "(no text)"]
        if self.answer:
            parts.append(ANSWER_PREFIX + self.answer)
        parts.extend(self.segments)
        text = "\n\n".join(parts)
        text = guard.redact(text, self.rules)
        if len(text) > self.max_chars:
            text = text[: self.max_chars] + TRUNCATED
        return Turn(turn=index, ts=ts or self.ts, kind="turn", text=text)


def parse(
    path: Path, *, max_chars: int = 6000, rules: tuple[guard.Rule, ...] | None = None
) -> ParsedSession:
    rules = guard.BUILTIN if rules is None else rules
    session_id = path.stem
    cwd: str | None = None
    started: str | None = None
    builder = _TurnBuilder(max_chars=max_chars, rules=rules, ts=None)
    turns: list[Turn] = []
    feedback: list[dict[str, Any]] = []
    tool_calls = 0
    user_turns = 0
    malformed = 0

    def flush(ts: str | None) -> None:
        nonlocal builder
        turn = builder.build(len(turns), ts)
        if turn is not None:
            turns.append(turn)
        builder = _TurnBuilder(max_chars=max_chars, rules=rules, ts=None)

    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(entry, dict):
                malformed += 1
                continue
            kind = entry.get("type")
            ts = entry.get("timestamp")
            if kind == "session":
                session_id = str(entry.get("id") or session_id)
                cwd = entry.get("cwd") or cwd
                started = ts or started
                continue
            if kind == "custom":
                if entry.get("customType") == "rsi/feedback" and isinstance(
                    entry.get("data"), dict
                ):
                    feedback.append(entry["data"])
                continue
            if kind in ("compaction", "branch_summary"):
                summary = str(entry.get("summary") or "").strip()
                if summary:
                    builder.add_segment(SUMMARY_PREFIX + _clip(summary, SUMMARY_CHARS))
                continue
            if kind != "message":
                continue
            message = entry.get("message")
            if not isinstance(message, dict):
                malformed += 1
                continue
            role = message.get("role")
            blocks = message.get("content")
            if role == "user":
                user_turns += 1
                flush(ts)
                builder.ts = ts
                builder.add_user(blocks_to_text(blocks))
            elif role == "assistant":
                if isinstance(blocks, list):
                    for block in blocks:
                        if not isinstance(block, dict):
                            continue
                        block_type = block.get("type")
                        if block_type == "text":
                            text = str(block.get("text") or "").strip()
                            if text:
                                builder.add_answer(text)
                                builder.add_segment(text)
                        elif block_type == "toolCall":
                            tool_calls += 1
                            name = block.get("name") or "tool"
                            args = block.get("arguments")
                            builder.add_segment(
                                f"[tool {name}] {_clip(_compact(args), TOOL_ARG_CHARS)}"
                            )
                elif isinstance(blocks, str):
                    builder.add_answer(blocks)
                    builder.add_segment(blocks)
            elif role == "toolResult":
                builder.add_segment("[result] " + _clip(blocks_to_text(blocks), TOOL_RESULT_CHARS))
            elif role == "system":
                continue
            else:
                text = blocks_to_text(blocks)
                if text.strip():
                    builder.add_segment(text)
    flush(None)

    record = SessionRecord(
        id=session_id,
        harness="pi",
        project=None,
        cwd=cwd,
        source=str(path),
        started_ts=started,
        tool_calls=tool_calls,
        user_turns=user_turns,
    )
    return ParsedSession(record=record, turns=turns, feedback=feedback, malformed_lines=malformed)


def blocks_to_text(blocks: Any) -> str:
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, list):
        return ""
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") in (None, "text"):
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _compact(value: Any) -> str:
    if isinstance(value, dict):
        keys = list(value.keys())
        head = ", ".join(f"{key}={_one_line(value[key])}" for key in keys[:4])
        if len(keys) > 4:
            head += ", …"
        return "{" + head + "}"
    return _one_line(value)


def _one_line(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return " ".join(str(text).split())


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"
