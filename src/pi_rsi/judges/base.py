"""Judge protocol.

A judge answers a batch of cheap questions about a session, candidate text or a past
turn, and the loop uses those answers instead of asking the expensive generator to
reconsider. Two question kinds:

``noul``    yes/no as a probability: ``{"type": "noul", "instructions": ...,"true": "yes",
            "false": "no"}`` answered with a number in ``[0, 1]``.
``choice``  one of a fixed set: ``{"type": "choice", "instructions": ..., "options": [...]}``
            answered with one option.

The protocol is deliberately small (it is the "System One" style of atomic judgement:
many independent questions, one call, no free-form reasoning in the response) so that
any backend can implement it in a few lines. An answer that is missing, out of range or
not one of the options invalidates the whole response: partial credit would let a
degraded backend pass as a working judge.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from ..jsonio import JsonError, parse_object

Answers = dict[str, float | str]
Questions = dict[str, dict[str, Any]]

PROMPT_HEADER = (
    "You answer questions about a software engineering session. Reply with one JSON object "
    'only, of the form {"answers": {"<question id>": <answer>}}. For noul questions the answer '
    "is a number from 0 to 1: the probability that the statement in 'instructions' is true. "
    "For choice questions the answer is exactly one of the listed options. Answer every "
    "question. Do not add commentary."
)


class JudgeError(Exception):
    pass


class Judge(Protocol):
    name: str

    def ask(self, questions: Questions) -> Answers: ...


def noul(instructions: str, *, true: str = "yes", false: str = "no") -> dict[str, Any]:
    return {"type": "noul", "instructions": instructions, "true": true, "false": false}


def choice(instructions: str, options: list[str]) -> dict[str, Any]:
    if not options:
        raise ValueError("choice question needs at least one option")
    return {"type": "choice", "instructions": instructions, "options": list(options)}


def prompt(questions: Questions) -> str:
    return PROMPT_HEADER + "\n\nQuestions:\n" + json.dumps(questions, ensure_ascii=False, indent=2)


def validate(questions: Questions, raw: Any) -> Answers:
    """Check a judge response against the questions it was asked."""
    if isinstance(raw, dict) and "answers" in raw:
        raw = raw["answers"]
    if not isinstance(raw, dict):
        raise JudgeError(f"expected an object of answers, got {type(raw).__name__}")
    answers: Answers = {}
    for name, question in questions.items():
        if name not in raw:
            raise JudgeError(f"missing answer for {name!r}")
        value = raw[name]
        if question["type"] == "noul":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise JudgeError(f"answer for {name!r} must be a number, got {value!r}")
            score = float(value)
            if not 0.0 <= score <= 1.0:
                raise JudgeError(f"answer for {name!r} is outside [0, 1]: {score}")
            answers[name] = score
        else:
            if not isinstance(value, str):
                raise JudgeError(f"answer for {name!r} must be a string, got {value!r}")
            option = value.strip()
            if option not in question["options"]:
                raise JudgeError(
                    f"answer for {name!r} is not one of {question['options']}: {option!r}"
                )
            answers[name] = option
    return answers


def probability(answers: Answers, name: str, default: float = 0.0) -> float:
    value = answers.get(name, default)
    return float(value) if isinstance(value, (int, float)) else default


def option(answers: Answers, name: str, default: str = "") -> str:
    value = answers.get(name, default)
    return value if isinstance(value, str) else default


def parse_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a possibly chatty response."""
    try:
        return parse_object(text)
    except JsonError as exc:
        raise JudgeError(str(exc)) from exc
