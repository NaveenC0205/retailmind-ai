"""LLM abstraction.

The platform never talks to a provider SDK directly. It asks the router for a
completion and gets back a Completion with token counts and a model id, both
of which end up on the span. That is what makes cost-per-task and
accuracy-per-model measurable on the same axis.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass
class CompletionRequest:
    prompt: str
    system: str = ""
    temperature: float = 0.0
    max_tokens: int = 900
    stop: Optional[list[str]] = None
    # Free-form hint the mock provider uses to pick a scripted behaviour.
    purpose: str = "chat"
    meta: dict = field(default_factory=dict)

    def fingerprint(self) -> str:
        import hashlib

        payload = json.dumps(
            {
                "prompt": self.prompt,
                "system": self.system,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "purpose": self.purpose,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:32]


@dataclass
class Completion:
    text: str
    model: str
    provider: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    finish_reason: str = "stop"
    raw: dict = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.tokens_in + self.tokens_out


class LLMError(RuntimeError):
    """Provider-level failure. The router decides whether to fall back."""


class LLMTimeout(LLMError):
    pass


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(self, req: CompletionRequest) -> Completion: ...


# ----------------------------------------------------------------------
# structured actions
# ----------------------------------------------------------------------

ACTION_TYPES = {"tool", "delegate", "retrieve", "answer", "refuse", "escalate"}


@dataclass
class AgentAction:
    type: str
    reasoning: str = ""
    tool: Optional[str] = None
    arguments: dict = field(default_factory=dict)
    agent: Optional[str] = None
    task: str = ""
    content: str = ""
    citations: list = field(default_factory=list)

    def is_terminal(self) -> bool:
        return self.type in {"answer", "refuse", "escalate"}


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def parse_action(text: str) -> AgentAction:
    """Parse a model completion into a structured action.

    A model that emits unparseable output is not a crash -- it degrades to an
    `answer` action carrying the raw text. Agent evaluators score that as a
    planning failure, which is exactly what it is, and the run still terminates.
    """
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
    match = _JSON_BLOCK.search(raw)
    if not match:
        return AgentAction(type="answer", content=raw, reasoning="unstructured-output")
    try:
        data: Any = json.loads(match.group(0))
    except json.JSONDecodeError:
        return AgentAction(type="answer", content=raw, reasoning="unparseable-json")
    if not isinstance(data, dict):
        return AgentAction(type="answer", content=raw, reasoning="non-object-json")

    atype = str(data.get("type", "answer")).lower()
    if atype not in ACTION_TYPES:
        atype = "answer"
    return AgentAction(
        type=atype,
        reasoning=str(data.get("reasoning", ""))[:800],
        tool=data.get("tool"),
        arguments=data.get("arguments") or {},
        agent=data.get("agent"),
        task=str(data.get("task", "")),
        content=str(data.get("content", "")),
        citations=list(data.get("citations") or []),
    )


# Rough INR cost per 1k tokens. Local models are free; the number exists so
# cost-per-task is comparable across configurations rather than accurate.
COST_PER_1K_INR = {
    "mock": 0.0,
    "ollama": 0.0,
    "openai": 0.15,
}


def estimate_cost_inr(provider: str, total_tokens: int) -> float:
    return round(COST_PER_1K_INR.get(provider, 0.0) * total_tokens / 1000.0, 6)
