"""Dataset registry.

Datasets are JSON files on disk with a version and a content checksum. The
checksum is the point: an eval number is only meaningful against a known
corpus, and "we changed the dataset and the score went up" is the most common
way an AI quality programme fools itself.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from app.config import get_settings


@dataclass
class Dataset:
    name: str
    kind: str
    version: str
    items: list[dict] = field(default_factory=list)
    checksum: str = ""
    path: Optional[Path] = None

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[dict]:
        return iter(self.items)

    def categories(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in self.items:
            out[i.get("category", "uncategorised")] = out.get(i.get("category", "uncategorised"), 0) + 1
        return dict(sorted(out.items()))

    def filter(self, **kw) -> "Dataset":
        items = [i for i in self.items if all(i.get(k) == v for k, v in kw.items())]
        return Dataset(self.name, self.kind, self.version, items, self.checksum, self.path)


def load(name: str, base: Optional[Path] = None) -> Dataset:
    """`name` is a path relative to datasets/, without .json.
    e.g. load("rag/policy_qa")"""
    s = get_settings()
    root = base or s.datasets_dir
    path = root / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"no dataset at {path}")
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    return Dataset(
        name=data.get("name", name),
        kind=data.get("kind", name.split("/")[0]),
        version=str(data.get("version", "1")),
        items=data.get("items", []),
        checksum=hashlib.sha256(raw.encode()).hexdigest()[:16],
        path=path,
    )


def load_all(kind: str, base: Optional[Path] = None) -> list[Dataset]:
    s = get_settings()
    root = (base or s.datasets_dir) / kind
    if not root.exists():
        return []
    return [load(f"{kind}/{p.stem}", base) for p in sorted(root.glob("*.json"))]


def available(base: Optional[Path] = None) -> list[str]:
    s = get_settings()
    root = base or s.datasets_dir
    if not root.exists():
        return []
    return sorted(
        str(p.relative_to(root)).removesuffix(".json") for p in root.rglob("*.json")
    )


def validate(ds: Dataset) -> list[str]:
    """Structural checks. Run in CI: a malformed dataset silently scoring 0 is
    indistinguishable from a broken system, which wastes a day every time."""
    problems: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(ds.items):
        loc = item.get("id", f"index {i}")
        if "id" not in item:
            problems.append(f"{loc}: missing id")
        elif item["id"] in seen:
            problems.append(f"{loc}: duplicate id")
        else:
            seen.add(item["id"])
        if "input" not in item or not isinstance(item["input"], dict):
            problems.append(f"{loc}: missing input object")
        elif ds.kind == "multiturn":
            turns = item["input"].get("turns")
            if not isinstance(turns, list) or not turns:
                problems.append(f"{loc}: input.turns must be a non-empty list")
            else:
                for index, turn in enumerate(turns):
                    if not isinstance(turn, dict) or turn.get("role") not in {"user", "assistant", "system"} or not isinstance(turn.get("text"), str) or not turn["text"].strip():
                        problems.append(f"{loc}: invalid turn {index}")
        elif "text" not in item["input"]:
            problems.append(f"{loc}: input.text is required")
        if "expected" not in item or not isinstance(item["expected"], dict):
            problems.append(f"{loc}: missing expected object")
        if not item.get("category"):
            problems.append(f"{loc}: missing category")
    return problems
