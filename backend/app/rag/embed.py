"""Embeddings.

`hashed` is the default: a deterministic, offline, dependency-free embedder
(hashed bag-of-ngrams, L2-normalised). It is not semantically strong, and it
is not pretending to be. It exists so that retrieval metrics are *reproducible
to the digit* in CI, on any machine, with no model download and no API bill.

Switch EMBEDDING_PROVIDER=ollama for real semantics. The index records which
embedder built it, and retrieval refuses to query an index built by a
different one -- a mismatched embedder is the classic silent RAG failure: no
error, just quietly terrible results.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

from app.config import get_settings

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


class Embedder(Protocol):
    name: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashedEmbedder:
    name = "hashed-v1"

    def __init__(self, dim: int | None = None):
        self.dim = dim or get_settings().embedding_dim

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        toks = tokenize(text)
        grams = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
        for gram in grams:
            h = hashlib.blake2b(gram.encode(), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "big") % self.dim
            sign = 1.0 if h[4] % 2 == 0 else -1.0
            # Sublinear term weighting, same idea as tf saturation in BM25.
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            return vec
        return [v / norm for v in vec]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class OllamaEmbedder:
    name = "ollama"

    def __init__(self):
        from app.llm.providers import OllamaLLM

        self._client = OllamaLLM()
        self.name = f"ollama:{get_settings().ollama_embed_model}"
        self.dim = 768

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vecs = await self._client.embed(texts)
        self.dim = len(vecs[0]) if vecs else self.dim
        return vecs


def get_embedder() -> Embedder:
    s = get_settings()
    if s.embedding_provider == "ollama":
        return OllamaEmbedder()
    return HashedEmbedder()


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
