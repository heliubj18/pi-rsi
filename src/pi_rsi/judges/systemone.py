"""A judge endpoint that speaks the System One protocol directly.

The protocol answers many atomic questions in one call, with a probability or a single
choice each, and no free-form text in the response::

    POST <base_url><path>
    {"state": {"source": "pi-rsi"},
     "model": "…",
     "questions": {"q": {"type": "noul", "instructions": "…",
                         "criteria": {"true": "…", "false": "…"}},
                   "r": {"type": "choice", "instructions": "…",
                         "criteria": {"option": "…", ...}}}}

    {"answers": {"q": {"type": "noul", "noul": 0.82},
                 "r": {"type": "choice", "choice": "option",
                       "probabilities": {"option": 0.9}, "confidence": 0.8}}}

``state`` is not interpreted by the endpoint, only required; the questions carry all the
context pi-rsi needs. Any other shape is an error, not a partial success: a choice answer
that is not the highest-probability option is rejected rather than trusted.
"""

from __future__ import annotations

from typing import Any

from .. import http
from ..config import JudgeConfig, resolve_secret
from .base import Answers, JudgeError, Questions, validate


class SystemOneJudge:
    name = "systemone"

    def __init__(self, cfg: JudgeConfig) -> None:
        self.cfg = cfg
        self.url = cfg.base_url.rstrip("/") + "/" + cfg.path.lstrip("/")

    def ask(self, questions: Questions) -> Answers:
        headers = {"authorization": f"Bearer {_secret(self.cfg)}"} if self.cfg.api_key else {}
        payload: dict[str, Any] = {"state": {"source": "pi-rsi"}, "questions": _wire(questions)}
        if self.cfg.model:
            payload["model"] = self.cfg.model
        try:
            response = http.post_json(
                self.url, payload, headers=headers, timeout=self.cfg.timeout_s
            )
        except http.HttpError as exc:
            raise JudgeError(str(exc)) from exc
        return validate(questions, {"answers": _answers(questions, response)})


def _wire(questions: Questions) -> dict[str, Any]:
    """Translate the neutral question shape into the one this protocol expects."""
    wire: dict[str, Any] = {}
    for name, question in questions.items():
        if question["type"] == "noul":
            wire[name] = {
                "type": "noul",
                "instructions": question["instructions"],
                "criteria": {
                    "true": question.get("true", "yes"),
                    "false": question.get("false", "no"),
                },
            }
        else:
            options = list(question["options"])
            wire[name] = {
                "type": "choice",
                "instructions": question["instructions"],
                "criteria": dict(
                    question.get("criteria") or {option: option for option in options}
                ),
            }
    return wire


def _answers(questions: Questions, payload: Any) -> dict[str, Any]:
    """Unwrap the protocol's per-answer objects into plain values for ``validate``."""
    answers = payload.get("answers") if isinstance(payload, dict) else None
    if not isinstance(answers, dict):
        raise JudgeError(f"expected {{'answers': ...}}, got {payload!r:.200}")
    plain: dict[str, Any] = {}
    for name, question in questions.items():
        answer = answers.get(name)
        if not isinstance(answer, dict):
            raise JudgeError(f"missing answer for {name!r}")
        if question["type"] == "noul":
            plain[name] = answer.get("noul")
            continue
        picked = answer.get("choice")
        probabilities = answer.get("probabilities")
        if isinstance(probabilities, dict) and probabilities:
            best = max(probabilities.items(), key=lambda item: item[1])[0]
            if picked != best:
                raise JudgeError(
                    f"{name}: choice {picked!r} is not the highest-probability option {best!r}"
                )
        plain[name] = picked
    return plain


def _secret(cfg: JudgeConfig) -> str:
    try:
        return resolve_secret(cfg.api_key)
    except Exception as exc:
        raise JudgeError(f"cannot resolve judge.api_key: {exc}") from exc
