"""The trace is the evaluation substrate.

Everything the platform decides is recorded as a span. Evaluators read spans,
not application internals -- which is why the same evaluator can score an
offline golden-set run and a sampled production request.

Spans are collected in-process during a request and flushed to ai_spans in one
write at the end. In the docker profile the same spans are also exported over
OTLP; nothing about the evaluation path changes.
"""
from __future__ import annotations

import contextvars
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from contextlib import contextmanager


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SpanRecord:
    id: str
    trace_id: str
    parent_id: Optional[str]
    name: str
    kind: str = "internal"
    status: str = "ok"
    started_at: datetime = field(default_factory=_now)
    duration_ms: int = 0
    attributes: dict = field(default_factory=dict)

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "trace_id": self.trace_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
        }


@dataclass
class Trace:
    trace_id: str
    spans: list[SpanRecord] = field(default_factory=list)

    def find(self, name: str) -> list[SpanRecord]:
        return [s for s in self.spans if s.name == name]

    def first(self, name: str) -> Optional[SpanRecord]:
        found = self.find(name)
        return found[0] if found else None

    def has(self, name: str) -> bool:
        return bool(self.find(name))

    def tool_names(self) -> list[str]:
        """Ordered list of tool names that actually EXECUTED (verdict allow).
        This is the trajectory that agent evaluators score."""
        return [
            s.attributes.get("tool.name", "")
            for s in self.spans
            if s.name == "tool.call" and s.attributes.get("gateway.verdict") == "allow"
        ]

    def attempted_tool_names(self) -> list[str]:
        """Every tool the agent *tried* to call, including refused ones.
        The difference between this and tool_names() is the whole security story."""
        return [s.attributes.get("tool.name", "") for s in self.spans if s.name == "tool.call"]

    def denials(self) -> list[SpanRecord]:
        return [
            s for s in self.spans
            if s.name == "tool.call" and s.attributes.get("gateway.verdict") != "allow"
        ]

    def retrieved_chunk_ids(self) -> list[str]:
        out: list[str] = []
        for s in self.find("rag.retrieve"):
            out.extend(s.attributes.get("chunk_ids", []))
        return out

    def reranked_chunk_ids(self) -> list[str]:
        out: list[str] = []
        for s in self.find("rag.retrieve"):
            out.extend(s.attributes.get("reranked_ids", []))
        return out

    def guardrail_verdicts(self) -> list[tuple[str, str]]:
        return [
            (s.attributes.get("check", ""), s.attributes.get("verdict", ""))
            for s in self.find("guardrail.check")
        ]

    def total_tokens(self) -> int:
        return sum(
            int(s.attributes.get("llm.tokens_in", 0)) + int(s.attributes.get("llm.tokens_out", 0))
            for s in self.find("llm.call")
        )

    def as_dicts(self) -> list[dict]:
        return [s.to_row() for s in self.spans]


_current: contextvars.ContextVar[Optional[Trace]] = contextvars.ContextVar(
    "retailmind_trace", default=None
)
_parent: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "retailmind_parent_span", default=None
)


def new_trace() -> Trace:
    t = Trace(trace_id="tr-" + uuid.uuid4().hex[:16])
    _current.set(t)
    _parent.set(None)
    return t


def current_trace() -> Optional[Trace]:
    return _current.get()


def set_trace(trace: Optional[Trace]) -> None:
    _current.set(trace)
    _parent.set(None)


@contextmanager
def span(name: str, kind: str = "internal", **attributes: Any) -> Iterator[SpanRecord]:
    trace = _current.get()
    rec = SpanRecord(
        id="sp-" + uuid.uuid4().hex[:12],
        trace_id=trace.trace_id if trace else "tr-orphan",
        parent_id=_parent.get(),
        name=name,
        kind=kind,
        attributes=dict(attributes),
    )
    token = _parent.set(rec.id)
    start = time.perf_counter()
    try:
        yield rec
    except Exception as exc:  # noqa: BLE001 -- span records the failure, then re-raises
        rec.status = "error"
        rec.attributes.setdefault("error.type", type(exc).__name__)
        rec.attributes.setdefault("error.message", str(exc)[:500])
        raise
    finally:
        rec.duration_ms = int((time.perf_counter() - start) * 1000)
        _parent.reset(token)
        if trace is not None:
            trace.spans.append(rec)


async def flush(trace: Trace) -> None:
    """Persist spans. Best-effort: a tracing failure must never fail a request."""
    if not trace.spans:
        return
    try:
        from app.db import session_scope
        from app.models import Span

        async with session_scope() as s:
            for row in trace.as_dicts():
                s.add(Span(**row))
    except Exception:  # pragma: no cover - defensive
        pass
