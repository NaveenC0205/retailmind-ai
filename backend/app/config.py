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


def supabase_postgres_url(
    project_url: str,
    password: str,
    *,
    region: str = "ap-northeast-1",
) -> str:
    """Build an async SQLAlchemy URI from a Supabase project URL + DB password.

    Uses the shared IPv4 pooler (transaction mode, port 6543). Direct
    ``db.<ref>.supabase.co`` is IPv6-only on the free tier and Vercel cannot
    reach it. Username must be ``postgres.<ref>`` on the pooler.
    """
    from urllib.parse import quote, urlparse

    host = (urlparse(project_url.strip()).hostname or "").strip()
    ref = host.split(".")[0] if host else ""
    if not ref or not password:
        return ""
    pw = quote(password, safe="")
    rg = (region or "ap-northeast-1").strip() or "ap-northeast-1"
    return (
        f"postgresql+asyncpg://postgres.{ref}:{pw}"
        f"@aws-0-{rg}.pooler.supabase.com:6543/postgres?ssl=require"
    )


def normalize_database_url(url: str) -> str:
    """Accept sqlite, postgres://, Supabase, and Neon URLs for SQLAlchemy async.

    Supabase / Neon dashboards copy `postgresql://...`. This app needs
    `postgresql+asyncpg://` plus SSL on hosted Postgres.
    """
    u = (url or "").strip().strip('"').strip("'")
    if not u:
        return u
    if u.startswith("postgres://"):
        u = "postgresql://" + u[len("postgres://") :]
    if u.startswith("postgresql://") and "+asyncpg" not in u.split("://", 1)[0]:
        u = "postgresql+asyncpg://" + u[len("postgresql://") :]
    hosted = any(h in u for h in ("supabase.co", "neon.tech", "pooler.supabase", "amazonaws.com"))
    if hosted and "ssl=" not in u.lower():
        u += ("&" if "?" in u else "?") + "ssl=require"
    return u


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
    # Supabase project (FastAPI uses Postgres URI, not the Next.js JS client).
    supabase_url: str = ""
    supabase_publishable_key: str = ""
    supabase_db_password: str = ""
    supabase_db_region: str = "ap-northeast-1"

    # --- llm -----------------------------------------------------------
    # mock    : deterministic, offline, zero cost. Used by CI.
    # ollama  : local models on your Mac (http://localhost:11434).
    # openai  : any OpenAI-compatible endpoint (OpenAI, Groq, vLLM, LM Studio).
    # groq    : free Groq Llama (maps to openai-compat).
    # gemini  : free Google Gemini (OpenAI-compat endpoint).
    # auto    : pick Groq → Gemini → OpenAI from whichever key is set.
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
    openai_model: str = "gpt-5.4"

    # Free hosted models — paste the key in Vercel; no code change needed.
    groq_api_key: str = ""
    groq_model: str = "llama-3.1-8b-instant"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"

    # Filled by bind_hosted_llm(): groq | gemini | openai | mock | ollama
    llm_backend: str = "mock"

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
    # Multi-agent runtime: langgraph (LangChain StateGraph supervisor team)
    multi_agent_framework: str = "langgraph"

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
    # Public origin for OpenAPI/Swagger "Try it out" (production first).
    public_base_url: str = "https://retailmind-ai-ten.vercel.app"

    # --- paths ---------------------------------------------------------
    kb_dir: Path = REPO_ROOT / "kb"
    prompts_dir: Path = REPO_ROOT / "prompts"
    datasets_dir: Path = REPO_ROOT / "datasets"
    static_dir: Path = REPO_ROOT / "backend" / "static"
    var_dir: Path = _DEFAULT_VAR

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_postgres(self) -> bool:
        return "postgresql" in (self.database_url or "")


def _hosted_deploy() -> bool:
    return bool(
        os.environ.get("VERCEL")
        or os.environ.get("VERCEL_ENV")
        or os.environ.get("RENDER")
        or os.environ.get("RAILWAY_ENVIRONMENT")
        or os.environ.get("FLY_APP_NAME")
    )


def bind_hosted_llm(s: Settings) -> Settings:
    """Map Groq / Gemini / OpenAI keys onto the OpenAI-compatible adapter.

    On Vercel, adding GROQ_API_KEY (recommended free model) is enough even if
    LLM_PROVIDER is still ``mock``. Local CI keeps mock because tests set it.
    """
    groq = (s.groq_api_key or "").strip()
    gemini = (s.gemini_api_key or "").strip()
    openai_key = (s.openai_api_key or "").strip()
    provider = (s.llm_provider or "mock").strip().lower()
    base = (s.openai_base_url or "").lower()

    if openai_key.startswith("gsk_") and not groq:
        groq = openai_key
    if "groq.com" in base and openai_key and not groq:
        groq = openai_key

    wants_auto = provider in {"auto", "free"}
    can_promote = wants_auto or provider in {"groq", "gemini"} or (
        provider == "mock" and _hosted_deploy() and bool(groq or gemini or openai_key)
    )

    def _as_openai(key: str, url: str, model: str, backend: str) -> None:
        object.__setattr__(s, "llm_provider", "openai")
        object.__setattr__(s, "openai_api_key", key)
        object.__setattr__(s, "openai_base_url", url.rstrip("/"))
        object.__setattr__(s, "openai_model", model)
        object.__setattr__(s, "llm_model", model)
        object.__setattr__(s, "llm_backend", backend)

    if provider == "groq" or (can_promote and groq and provider not in {"gemini", "openai", "ollama"}):
        if groq:
            _as_openai(groq, "https://api.groq.com/openai/v1", s.groq_model, "groq")
            return s
    if provider == "gemini" or (can_promote and gemini and provider not in {"openai", "ollama"}):
        if gemini:
            _as_openai(gemini, s.gemini_base_url, s.gemini_model, "gemini")
            return s
    if can_promote and openai_key and provider not in {"ollama"}:
        backend = "groq" if "groq.com" in base else "openai"
        model = s.openai_model if s.openai_model not in {"mock-1", ""} else (
            s.groq_model if backend == "groq" else "gpt-5.4"
        )
        url = s.openai_base_url
        if backend == "groq" and "groq.com" not in url.lower():
            url = "https://api.groq.com/openai/v1"
        _as_openai(openai_key, url, model, backend)
        return s

    if provider in {"openai", "openai-compat"} and openai_key:
        object.__setattr__(s, "llm_backend", "groq" if "groq.com" in base else "openai")
        object.__setattr__(s, "llm_model", s.openai_model)
        return s
    if provider == "ollama":
        object.__setattr__(s, "llm_backend", "ollama")
        return s
    object.__setattr__(s, "llm_backend", "mock")
    return s


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    url = (s.supabase_url or os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or "").strip()
    key = (
        s.supabase_publishable_key
        or os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
        or ""
    ).strip()
    if url:
        object.__setattr__(s, "supabase_url", url)
    if key:
        object.__setattr__(s, "supabase_publishable_key", key)
    if s.supabase_db_password and url and (s.is_sqlite or not s.database_url):
        built = supabase_postgres_url(
            url, s.supabase_db_password, region=s.supabase_db_region
        )
        if built:
            object.__setattr__(s, "database_url", built)
    object.__setattr__(s, "database_url", normalize_database_url(s.database_url) or s.database_url)
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
        if s.is_sqlite:
            object.__setattr__(s, "database_url", "sqlite+aiosqlite:////tmp/retailmind.db")
    return bind_hosted_llm(s)


def reset_settings_cache() -> None:
    get_settings.cache_clear()
