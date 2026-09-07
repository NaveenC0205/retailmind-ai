"""LLM router: provider selection, fallback, cassettes, tracing, cost.

Every completion in the platform goes through here, so every completion lands
on a span with model, prompt version, token counts and cost. That is what
makes "compare model A and B on the same dataset" a query rather than a
project.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from app.config import get_settings
from app.llm.base import (
    AgentAction,
    Completion,
    CompletionRequest,
    LLMError,
    estimate_cost_inr,
    parse_action,
)
from app.llm.mock import MockLLM
from app.tracing import span


class Cassette:
    """Record/replay of provider calls, keyed by request fingerprint.

    This is the determinism seam. With cassettes the agent suite runs on every
    commit in seconds; without them it is a slow, flaky nightly job that people
    stop trusting.
    """

    def __init__(self, path: Path, mode: str):
        self.path = path
        self.mode = mode
        self.data: dict[str, dict] = {}
        if mode in ("replay", "record") and path.exists():
            try:
                self.data = json.loads(path.read_text())
            except json.JSONDecodeError:
                self.data = {}

    def get(self, key: str) -> Optional[Completion]:
        row = self.data.get(key)
        if not row:
            return None
        return Completion(**row)

    def put(self, key: str, completion: Completion) -> None:
        self.data[key] = {
            "text": completion.text,
            "model": completion.model,
            "provider": completion.provider,
            "tokens_in": completion.tokens_in,
            "tokens_out": completion.tokens_out,
            "latency_ms": completion.latency_ms,
            "finish_reason": completion.finish_reason,
        }

    def save(self) -> None:
        if self.mode != "record":
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True))


def _resolved_model(name: str, model: Optional[str] = None) -> Optional[str]:
    """Pick the real model for a provider. Ignore the default mock-1 name."""
    if model and model not in ("mock-1", "mock-naive-1"):
        return model
    s = get_settings()
    if name == "ollama":
        return s.ollama_model
    if name in ("openai", "openai-compat"):
        return s.openai_model
    return None


def build_provider(name: str, model: Optional[str] = None):
    name = (name or "mock").lower()
    resolved = _resolved_model(name, model)
    if name == "mock":
        return MockLLM(model=resolved or "mock-1")
    if name == "mock-naive":
        return MockLLM(model=resolved or "mock-naive-1", naive=True)
    if name == "ollama":
        from app.llm.providers import OllamaLLM

        return OllamaLLM(model=resolved)
    if name in ("openai", "openai-compat"):
        from app.llm.providers import OpenAICompatLLM

        return OpenAICompatLLM(model=resolved)
    raise ValueError(f"unknown llm provider: {name}")


class LLMRouter:
    def __init__(
        self,
        provider=None,
        fallback=None,
        cassette: Optional[Cassette] = None,
        prompt_version: str = "v1",
        config_name: str = "default",
    ):
        s = get_settings()
        self.primary = provider or build_provider(s.llm_provider, s.llm_model if s.llm_provider != "mock" else None)
        self.fallback = fallback
        if self.fallback is None and s.llm_fallback_provider and s.llm_fallback_provider != s.llm_provider:
            try:
                self.fallback = build_provider(s.llm_fallback_provider)
            except Exception:  # pragma: no cover
                self.fallback = None
        self.cassette = cassette
        self.prompt_version = prompt_version
        self.config_name = config_name
        self.total_tokens_in = 0
        self.total_tokens_out = 0
        self.total_cost_inr = 0.0
        self.call_count = 0

    @property
    def model_name(self) -> str:
        return getattr(self.primary, "model", "unknown")

    @property
    def provider_name(self) -> str:
        return getattr(self.primary, "name", "unknown")

    async def complete(self, req: CompletionRequest) -> Completion:
        key = req.fingerprint()
        with span(
            "llm.call",
            kind="client",
            purpose=req.purpose,
            **{
                "llm.provider": self.provider_name,
                "llm.model": self.model_name,
                "llm.prompt_version": self.prompt_version,
                "llm.config": self.config_name,
                "llm.fingerprint": key,
            },
        ) as sp:
            if self.cassette and self.cassette.mode == "replay":
                cached = self.cassette.get(key)
                if cached is not None:
                    sp.attributes["llm.cassette"] = "hit"
                    self._account(cached, sp)
                    return cached
                sp.attributes["llm.cassette"] = "miss"

            started = time.perf_counter()
            try:
                completion = await self.primary.complete(req)
                sp.attributes["llm.fallback_used"] = False
            except LLMError as exc:
                sp.attributes["llm.primary_error"] = type(exc).__name__
                sp.attributes["llm.primary_error_detail"] = str(exc)[:300]
                if self.fallback is None:
                    raise
                completion = await self.fallback.complete(req)
                sp.attributes["llm.fallback_used"] = True
                sp.attributes["llm.model"] = completion.model
            completion.latency_ms = completion.latency_ms or int(
                (time.perf_counter() - started) * 1000
            )

            if self.cassette and self.cassette.mode == "record":
                self.cassette.put(key, completion)
            self._account(completion, sp)
            return completion

    def _account(self, completion: Completion, sp) -> None:
        self.call_count += 1
        self.total_tokens_in += completion.tokens_in
        self.total_tokens_out += completion.tokens_out
        cost = estimate_cost_inr(completion.provider, completion.total_tokens)
        self.total_cost_inr += cost
        sp.attributes.update(
            {
                "llm.tokens_in": completion.tokens_in,
                "llm.tokens_out": completion.tokens_out,
                "llm.cost_inr": cost,
                "llm.finish_reason": completion.finish_reason,
                "llm.latency_ms": completion.latency_ms,
            }
        )

    async def decide(self, req: CompletionRequest) -> AgentAction:
        req.purpose = "agent_step"
        completion = await self.complete(req)
        action = parse_action(completion.text)
        return action
