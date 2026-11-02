"""Generator backed by a one-shot ``pi`` process.

No SDK, no HTTP client, no model list: the same CLI the user already has, with tools,
extensions, skills and context files disabled so the call is reproducible and cannot
recurse into itself. The model is whatever ``generator.model`` says; empty means pi's
configured default, so a user who never writes a config still gets a working loop.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from ..config import GeneratorConfig
from .base import GeneratorError


class PiGenerator:
    name = "pi"

    def __init__(self, cfg: GeneratorConfig) -> None:
        self.cfg = cfg
        self.binary = os.environ.get("RSI_PI_BIN", "pi")

    def generate(self, system: str, prompt: str) -> str:
        path = shutil.which(self.binary)
        if path is None:
            raise GeneratorError(
                f"cannot find the {self.binary!r} executable; install pi or set generator.provider "
                "to 'openai'/'anthropic' with a base_url and api_key"
            )
        args = [
            path,
            "--print",
            "--no-session",
            "--no-tools",
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-context-files",
            "--system-prompt",
            system,
        ]
        if self.cfg.model:
            args.extend(["--model", self.cfg.model])
        env = os.environ.copy()
        env["RSI_LEARN"] = "0"  # never let a learning call trigger another
        env.pop("PI_SESSION_FILE", None)
        try:
            result = subprocess.run(
                args,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.cfg.timeout_s,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GeneratorError(f"pi did not answer within {self.cfg.timeout_s:g}s") from exc
        except OSError as exc:
            raise GeneratorError(f"cannot run pi: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[-1500:]
            raise GeneratorError(f"pi exited with {result.returncode}: {detail}")
        return result.stdout
