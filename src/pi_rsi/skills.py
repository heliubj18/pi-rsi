"""Learned skills.

A skill is a directory with a ``SKILL.md``: YAML frontmatter with ``name`` and
``description``, then the procedure. The format is pi's, so a learned skill is a plain
skill file that the extension hands to pi through ``resources_discover``; nothing here
depends on pi internals beyond that format.

Learned skills live under ``$RSI_HOME`` and never in the package, because ``pi update``
reconciles the checkout. Every write is preceded by a copy into ``state/snapshots`` so a
bad lesson can be rolled back without a git revert.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import guard
from .paths import GLOBAL_SCOPE, Paths

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_BODY_CHARS = 6000
MAX_BODY_LINES = 120
FRONTMATTER = "---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"


class SkillError(Exception):
    pass


@dataclass(slots=True)
class LearnedSkill:
    name: str
    description: str
    body: str
    path: Path
    scope: str
    mtime: float


def _skill_dir(paths: Paths, scope: str, name: str) -> Path:
    return paths.skills_for(scope) / name


def available(paths: Paths, scope: str) -> dict[str, str]:
    """Names already taken in this scope, mapped to the file that took them."""
    taken: dict[str, str] = {}
    root = paths.skills_for(scope)
    if root.is_dir():
        for entry in sorted(root.iterdir()):
            file = entry / "SKILL.md"
            if file.exists():
                taken[entry.name] = str(file)
    return taken


def read(path: Path, scope: str) -> LearnedSkill:
    text = path.read_text(encoding="utf-8")
    meta, body = parse(text)
    name = str(meta.get("name") or path.parent.name)
    return LearnedSkill(
        name=name,
        description=str(meta.get("description") or ""),
        body=body,
        path=path,
        scope=scope,
        mtime=path.stat().st_mtime,
    )


def parse(text: str) -> tuple[dict[str, str], str]:
    """Minimal frontmatter reader: ``key: value`` pairs, no nested structures."""
    if not text.startswith("---"):
        return {}, text.strip()
    lines = text.splitlines()
    meta: dict[str, str] = {}
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == "---":
            return meta, "\n".join(lines[index + 1 :]).strip()
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip("'\"")
    return {}, text.strip()


def list_learned(paths: Paths, scope: str | None = None) -> list[LearnedSkill]:
    skills: list[LearnedSkill] = []
    scopes = [scope] if scope else [GLOBAL_SCOPE, *(name for name, _ in paths.project_skill_dirs())]
    for current in scopes:
        root = paths.skills_for(current)
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            file = entry / "SKILL.md"
            if file.is_file():
                try:
                    skills.append(read(file, current))
                except OSError:
                    continue
    return skills


def validate(
    name: str,
    description: str,
    body: str,
    *,
    rules: tuple[guard.Rule, ...] = guard.BUILTIN,
) -> list[str]:
    errors: list[str] = []
    if len(name) > MAX_NAME:
        errors.append(f"name exceeds {MAX_NAME} characters")
    if not NAME_RE.match(name):
        errors.append(
            "name must be lowercase a-z, 0-9 and single hyphens, e.g. 'flaky-test-triage'"
        )
    description = description.strip()
    if not description:
        errors.append("description is required")
    elif len(description) > MAX_DESCRIPTION:
        errors.append(f"description exceeds {MAX_DESCRIPTION} characters")
    elif "\n" in description:
        errors.append("description must be a single line")
    body = body.strip()
    if not body:
        errors.append("body is required")
    elif len(body) > MAX_BODY_CHARS:
        errors.append(f"body exceeds {MAX_BODY_CHARS} characters")
    elif len(body.splitlines()) > MAX_BODY_LINES:
        errors.append(f"body exceeds {MAX_BODY_LINES} lines")
    for label, value in (("name", name), ("description", description), ("body", body)):
        if (rule := guard.find_secret(value, rules)) is not None:
            errors.append(f"{label} matches the secret pattern {rule!r}; refusing to write it")
    return errors


def render(name: str, description: str, body: str) -> str:
    return FRONTMATTER.format(name=name, description=description.strip(), body=body.strip())


def write(
    paths: Paths,
    *,
    name: str,
    description: str,
    body: str,
    scope: str = GLOBAL_SCOPE,
    overwrite: bool = True,
    rules: tuple[guard.Rule, ...] = guard.BUILTIN,
) -> Path:
    errors = validate(name, description, body, rules=rules)
    if errors:
        raise SkillError("; ".join(errors))
    directory = _skill_dir(paths, scope, name)
    file = directory / "SKILL.md"
    if file.exists() and not overwrite:
        raise SkillError(f"skill {name!r} already exists in scope {scope!r}")
    if file.exists():
        snapshot(paths, file, scope=scope, name=name)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = file.with_suffix(".md.tmp")
    tmp.write_text(render(name, description, body), encoding="utf-8")
    tmp.replace(file)
    return file


def remove(paths: Paths, name: str, scope: str = GLOBAL_SCOPE) -> bool:
    directory = _skill_dir(paths, scope, name)
    file = directory / "SKILL.md"
    if not file.exists():
        return False
    snapshot(paths, file, scope=scope, name=name)
    shutil.rmtree(directory)
    return True


def snapshot(paths: Paths, file: Path, *, scope: str, name: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = paths.snapshots / stamp / scope / name / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copy2(file, target)
    return target


def render_index(skills: list[LearnedSkill], *, limit_chars: int = 2000) -> str:
    """One line per skill, for the learner prompt (so it does not duplicate a lesson)."""
    lines: list[str] = []
    used = 0
    for skill in skills:
        line = f"- {skill.name} [{skill.scope}]: {skill.description}"
        if used + len(line) > limit_chars:
            lines.append("- (more skills exist; see `rsi skills`)")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)
