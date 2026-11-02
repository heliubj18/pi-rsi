"""A judge endpoint that speaks the System One protocol directly.

Request::

    POST <base_url><path>
    {"model": "…", "questions": {"q": {"type": "noul", "instructions": …}}}

Response::

    {"answers": {"q": 0.82}}

Any other shape is an error, not a partial success.
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
        payload: dict[str, Any] = {"questions": questions}
        if self.cfg.model:
            payload["model"] = self.cfg.model
        try:
            response = http.post_json(
                self.url, payload, headers=headers, timeout=self.cfg.timeout_s
            )
        except http.HttpError as exc:
            raise JudgeError(str(exc)) from exc
        return validate(questions, response)


def _secret(cfg: JudgeConfig) -> str:
    try:
        return resolve_secret(cfg.api_key)
    except Exception as exc:
        raise JudgeError(f"cannot resolve judge.api_key: {exc}") from exc
