"""Shared fixtures: an isolated ``$RSI_HOME`` and a session file builder."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from pi_rsi import facts, guard, store
from pi_rsi.config import Config, load
from pi_rsi.paths import Paths


@pytest.fixture(autouse=True)
def _isolated_pi_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Never read the developer's real pi settings or session directory."""
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.delenv("PI_CODING_AGENT_SESSION_DIR", raising=False)
    monkeypatch.delenv("RSI_SESSIONS_DIR", raising=False)
    monkeypatch.delenv("RSI_HOME", raising=False)


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    home = tmp_path / "rsi-home"
    resolved = Paths(home)
    resolved.ensure()
    return resolved


@pytest.fixture
def cfg(paths: Paths) -> Config:
    return load(paths)


@pytest.fixture
def cfg_with_judge(paths: Paths) -> Config:
    base = load(paths)
    return replace(
        base, judge=replace(base.judge, provider="systemone", base_url="http://judge.test")
    )


def make_session(
    path: Path,
    *,
    cwd: str = "/tmp/proj-a",
    session_id: str = "s-0001",
    pairs: list[tuple[str, str]] | None = None,
    tool_calls: int = 3,
    custom: list[dict] | None = None,
    started: str = "2026-01-01T00:00:00.000Z",
) -> Path:
    """Write a pi session JSONL with one or more user/assistant pairs."""
    pairs = pairs or [("why does the deploy fail?", "The image tag is stale.")]
    lines: list[dict] = [
        {"type": "session", "version": 1, "id": session_id, "timestamp": started, "cwd": cwd}
    ]
    for index, (question, answer) in enumerate(pairs):
        lines.append(
            {
                "type": "message",
                "timestamp": f"2026-01-01T00:0{index}:00.000Z",
                "message": {"role": "user", "content": [{"type": "text", "text": question}]},
            }
        )
        content: list[dict] = []
        for call in range(tool_calls):
            content.append(
                {
                    "type": "toolCall",
                    "id": f"call-{index}-{call}",
                    "name": "bash",
                    "arguments": {"command": f"echo step {call}"},
                }
            )
        content.append({"type": "text", "text": answer})
        lines.append(
            {
                "type": "message",
                "timestamp": f"2026-01-01T00:0{index}:30.000Z",
                "message": {"role": "assistant", "content": content},
            }
        )
        lines.append(
            {
                "type": "message",
                "timestamp": f"2026-01-01T00:0{index}:40.000Z",
                "message": {
                    "role": "toolResult",
                    "content": [{"type": "text", "text": "step output"}],
                },
            }
        )
    for entry in custom or []:
        lines.append(entry)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


class FakeGenerator:
    """Returns a fixed payload and records what it was asked."""

    name = "fake"

    def __init__(self, payload: dict | str) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    def generate(self, system: str, prompt: str) -> str:
        self.calls.append((system, prompt))
        return self.payload if isinstance(self.payload, str) else json.dumps(self.payload)


class FakeJudge:
    """Answers from a table; anything unlisted gets a permissive default."""

    name = "fake"

    def __init__(self, answers: dict[str, float | str] | None = None, default: float = 1.0) -> None:
        self.answers = answers or {}
        self.default = default
        self.calls: list[dict] = []

    def ask(self, questions):
        self.calls.append(questions)
        out: dict[str, float | str] = {}
        for name, question in questions.items():
            if name in self.answers:
                out[name] = self.answers[name]
            elif question["type"] == "noul":
                out[name] = self.default
            else:
                out[name] = question["options"][0]
        return out


@pytest.fixture
def payload() -> dict:
    return {
        "skill": {
            "name": "stale-image-tag-triage",
            "description": "Find a stale image tag when a deploy fails right after a merge",
            "body": "1. Read the failing step.\n2. Compare the tag with the merge commit.\n3. Re-tag.",
            "scope": "project",
        },
        "facts": {
            "add": [
                {
                    "text": "Deploys use the image tag from the merge commit, not from HEAD.",
                    "scope": "project",
                }
            ]
        },
        "reason": "one procedure and one durable fact",
    }


@pytest.fixture
def clean_rules() -> tuple[guard.Rule, ...]:
    return guard.BUILTIN


@pytest.fixture
def seeded_fact(paths: Paths) -> facts.Fact:
    result = facts.add(paths, "The index is rebuilt with rsi reindex.", scope="global")
    assert result.fact is not None
    return result.fact


__all__ = [
    "FakeGenerator",
    "FakeJudge",
    "make_session",
    "store",
]
