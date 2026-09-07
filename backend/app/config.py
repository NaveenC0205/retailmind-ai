"""Runtime configuration.

Two runtime profiles:

  local   (default)  SQLite + in-process vector/BM25 search + in-process bus.
                     Runs with `pip install -e .` and nothing else. This is the
                     profile the test suite and CI use.

  docker  (opt-in)   Postgres+pgvector, Redis, OpenSearch, Redpanda, OTel.
                     See infrastructure/docker-compose.yml.

The profile never changes behaviour that evaluation measures -- only where
bytes are stored. If a metric moves when you switch profile, that is a bug.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

# Vercel serverless: only /tmp is writable between requests on the same instance.
_ON_VERCEL = bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))
_DEFAULT_VAR = Path("/tmp/retailmind") if _ON_VERCEL else (REPO_ROOT / "var")
_DEFAULT_DB = (
    "sqlite+aiosqlite:////tmp/retailmind.db"
    if _ON_VERCEL
    else f"sqlite+aiosqlite:///{REPO_ROOT / 'var' / 'retailmind.db'}"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("RETAILMIND_ENV_FILE", str(REPO_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        # Vercel injects empty strings for unset dashboard env vars; treat those
        # as missing so field defaults apply (otherwise bool/int/float parse fail).
        env_ignore_empty=True,
    )

    # --- runtime -------------------------------------------------------
    profile: str = "local"
    app_name: str = "RetailMind AI"
    debug: bool = False

    # --- storage -------------------------------------------------------
    database_url: str = _DEFAULT_DB

    # --- llm -----------------------------------------------------------
    # mock    : deterministic, offline, zero cost. Used by CI.
    # ollama  : local models on your Mac (http://localhost:11434).
    # openai  : any OpenAI-compatible endpoint (OpenAI, Groq, vLLM, LM Studio).
    llm_provider: str = "mock"
    llm_model: str = "mock-1"
    llm_fallback_provider: str = "mock"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 900
    llm_timeout_s: float = 60.0

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_embed_model: str = "nomic-embed-text"

    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # --- embeddings ----------------------------------------------------
    # hashed : deterministic offline embedder (default; reproducible evals)
    # ollama : real embeddings via ollama_embed_model
    embedding_provider: str = "hashed"
    embedding_dim: int = 384

    # --- cassettes -----------------------------------------------------
    # off / record / replay  -- the determinism seam for agent tests.
    cassette_mode: str = "off"
    cassette_dir: Path = REPO_ROOT / "tests" / "cassettes"

    # --- agent budgets -------------------------------------------------
    max_steps: int = 20
    max_tokens_per_run: int = 60000
    max_wall_clock_s: float = 90.0

    # --- policy --------------------------------------------------------
    hitl_refund_threshold_inr: int = 50000
    memory_decay_days: int = 180
    retrieval_top_k: int = 50
    rerank_top_k: int = 6
    retrieval_min_score: float = 0.25

    # --- deployment ----------------------------------------------------
    # Cloud hosts (Render, Fly, Cloud Run, HF Spaces) inject PORT.
    port: int = 8000
    # A fresh container has an empty database. Seed and ingest on first boot so
    # the deployed URL is useful immediately rather than an empty shell.
    auto_bootstrap: bool = True
    # Edge protections. Off by default locally, on in the container image.
    rate_limit_per_min: int = 0
    rate_limit_burst: int = 10
    daily_request_limit: int = 0
    access_password: str = ""
    public_demo: bool = False

    # --- paths ---------------------------------------------------------
    kb_dir: Path = REPO_ROOT / "kb"
    prompts_dir: Path = REPO_ROOT / "prompts"
    datasets_dir: Path = REPO_ROOT / "datasets"
    static_dir: Path = REPO_ROOT / "backend" / "static"
    var_dir: Path = _DEFAULT_VAR

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    # Vercel (and similar) ship a read-only filesystem except /tmp. If our
    # configured var_dir is not writable, fall back so import/startup cannot crash.
    try:
        s.var_dir.mkdir(parents=True, exist_ok=True)
        probe = s.var_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        tmp = Path("/tmp/retailmind")
        tmp.mkdir(parents=True, exist_ok=True)
        object.__setattr__(s, "var_dir", tmp)
        object.__setattr__(s, "database_url", "sqlite+aiosqlite:////tmp/retailmind.db")
    return s


def reset_settings_cache() -> None:
    get_settings.cache_clear()
