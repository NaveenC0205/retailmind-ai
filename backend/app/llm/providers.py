"""Real providers: Ollama (local) and any OpenAI-compatible endpoint.

Both are thin. Everything that matters -- retries, fallback, cost, tracing --
lives in the router, so adding a provider never changes platform behaviour.
"""
from __future__ import annotations

import time

import httpx

from app.config import get_settings
from app.llm.base import Completion, CompletionRequest, LLMError, LLMTimeout


def uses_max_completion_tokens(model: str) -> bool:
    """GPT-5 / o-series reject max_tokens and often reject temperature."""
    m = (model or "").lower()
    return m.startswith(("gpt-5", "o1", "o3", "o4"))


def openai_chat_payload(model: str, req: CompletionRequest) -> dict:
    messages = []
    if req.system:
        messages.append({"role": "system", "content": req.system})
    messages.append({"role": "user", "content": req.prompt})
    payload: dict = {"model": model, "messages": messages}
    tokens = max(int(req.max_tokens or 900), 1)
    if uses_max_completion_tokens(model):
        # Reasoning tokens share this budget; keep a floor so the visible reply is not empty.
        payload["max_completion_tokens"] = max(tokens, 1600)
    else:
        payload["temperature"] = req.temperature
        payload["max_tokens"] = tokens
    if req.purpose in ("agent_step", "judge", "memory_extract"):
        payload["response_format"] = {"type": "json_object"}
    return payload


class OllamaLLM:
    """Local models via http://localhost:11434.

    Run on your Mac:
        ollama pull llama3.1:8b
        ollama pull nomic-embed-text
        LLM_PROVIDER=ollama make dev
    """

    name = "ollama"

    def __init__(self, model: str | None = None, base_url: str | None = None):
        s = get_settings()
        self.model = model or s.ollama_model
        self.base_url = (base_url or s.ollama_base_url).rstrip("/")
        self.timeout = s.llm_timeout_s

    async def complete(self, req: CompletionRequest) -> Completion:
        started = time.perf_counter()
        payload = {
            "model": self.model,
            "prompt": req.prompt,
            "system": req.system,
            "stream": False,
            "options": {"temperature": req.temperature, "num_predict": req.max_tokens},
        }
        if req.purpose in ("agent_step", "judge", "memory_extract"):
            payload["format"] = "json"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(f"{self.base_url}/api/generate", json=payload)
                r.raise_for_status()
                data = r.json()
        except httpx.TimeoutException as exc:
            raise LLMTimeout(f"ollama timeout after {self.timeout}s") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"ollama error: {exc}") from exc
        return Completion(
            text=data.get("response", ""),
            model=self.model,
            provider=self.name,
            tokens_in=int(data.get("prompt_eval_count", 0)),
            tokens_out=int(data.get("eval_count", 0)),
            latency_ms=int((time.perf_counter() - started) * 1000),
            finish_reason=data.get("done_reason", "stop"),
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        s = get_settings()
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for t in texts:
                r = await client.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": s.ollama_embed_model, "prompt": t},
                )
                r.raise_for_status()
                out.append(r.json()["embedding"])
        return out


class OpenAICompatLLM:
    """OpenAI, Groq, vLLM, LM Studio -- anything speaking /v1/chat/completions."""

    name = "openai"

    def __init__(self, model: str | None = None):
        s = get_settings()
        self.model = model or s.openai_model
        self.base_url = s.openai_base_url.rstrip("/")
        self.api_key = s.openai_api_key
        self.timeout = s.llm_timeout_s

    async def complete(self, req: CompletionRequest) -> Completion:
        if not self.api_key:
            raise LLMError("openai_api_key is not set")
        started = time.perf_counter()
        payload: dict = openai_chat_payload(self.model, req)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                r.raise_for_status()
                data = r.json()
        except httpx.TimeoutException as exc:
            raise LLMTimeout(f"openai timeout after {self.timeout}s") from exc
        except httpx.HTTPStatusError as exc:
            body = (exc.response.text or "")[:400]
            raise LLMError(f"openai error: {exc.response.status_code} {body}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"openai error: {exc}") from exc
        usage = data.get("usage", {}) or {}
        return Completion(
            text=data["choices"][0]["message"]["content"] or "",
            model=self.model,
            provider=self.name,
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            latency_ms=int((time.perf_counter() - started) * 1000),
            finish_reason=data["choices"][0].get("finish_reason", "stop"),
        )
