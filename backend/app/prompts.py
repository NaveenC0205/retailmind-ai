"""Prompt registry.

Prompts are versioned files on disk, loaded by (version, name) and cached.
They are immutable once released: a change means a new version directory, so
`eval_runs.prompt_version` is meaningful and a regression is attributable.

Never build a prompt by string-concatenating user input here. User input
enters through provenance envelopes in app/provenance.py.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.config import get_settings


class PromptNotFound(KeyError):
    pass


@lru_cache(maxsize=256)
def get_prompt(name: str, version: str = "v1") -> str:
    s = get_settings()
    path: Path = s.prompts_dir / version / f"{name}.md"
    if not path.exists():
        fallback = s.prompts_dir / "v1" / f"{name}.md"
        if not fallback.exists():
            raise PromptNotFound(f"no prompt '{name}' in {version} or v1")
        return fallback.read_text(encoding="utf-8").strip()
    return path.read_text(encoding="utf-8").strip()


def available_versions() -> list[str]:
    s = get_settings()
    if not s.prompts_dir.exists():
        return []
    return sorted(p.name for p in s.prompts_dir.iterdir() if p.is_dir())


def prompt_names(version: str = "v1") -> list[str]:
    s = get_settings()
    d = s.prompts_dir / version
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.md"))


def clear_cache() -> None:
    get_prompt.cache_clear()
