"""Generator protocol.

A generator turns a digest of a finished session into candidates: at most one skill and
a small number of facts. It is called once per session and is the expensive part of the
loop, which is why the judge gates it first.
"""

from __future__ import annotations

from typing import Protocol

from ..jsonio import JsonError, parse_object


class GeneratorError(Exception):
    pass


class Generator(Protocol):
    name: str

    def generate(self, system: str, prompt: str) -> str: ...


def parse_output(text: str) -> dict:
    try:
        return parse_object(text)
    except JsonError as exc:
        raise GeneratorError(str(exc)) from exc
