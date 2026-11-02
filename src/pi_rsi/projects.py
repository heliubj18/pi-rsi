"""Directory to project mapping.

A project is a name for one or more working directories. Scoping matters: knowledge
about one repository is often wrong in another, so facts and skills default to the
project and only become global when the judge is confident they generalize.

``$RSI_HOME/projects.json``::

    {
      "gravity": ["~/git/github.com/novitalabs/gravity"],
      "webapp":  ["~/src/webapp", "~/src/webapp-infra"]
    }
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import ConfigError
from .paths import Paths


def load(paths: Paths) -> dict[str, list[Path]]:
    path = paths.projects_file
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected an object mapping project name to directories")
    mapping: dict[str, list[Path]] = {}
    for name, entries in raw.items():
        if not isinstance(name, str) or not name or "/" in name:
            raise ConfigError(f"{path}: invalid project name {name!r}")
        if isinstance(entries, str):
            entries = [entries]
        if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
            raise ConfigError(f"{path}: {name} must be a list of directories")
        mapping[name] = [Path(entry).expanduser().resolve() for entry in entries]
    return mapping


def resolve(cwd: str | Path | None, *, mapping: dict[str, list[Path]] | None = None) -> str | None:
    """Longest matching directory prefix wins; falls back to the git toplevel."""
    if not cwd:
        return None
    try:
        target = Path(cwd).expanduser().resolve()
    except OSError:
        return None
    mapping = mapping or {}
    best: tuple[int, str] | None = None
    for name, directories in mapping.items():
        for directory in directories:
            if target == directory or directory in target.parents:
                depth = len(directory.parts)
                if best is None or depth > best[0]:
                    best = (depth, name)
    if best is not None:
        return best[1]
    toplevel = _git_toplevel(target)
    if toplevel is not None and toplevel != target:
        return resolve(toplevel, mapping=mapping)
    return None


def _git_toplevel(path: Path) -> Path | None:
    if not path.exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return Path(out).resolve() if out else None
