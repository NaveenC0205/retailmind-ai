"""OpenAPI / Swagger catalog for every HTTP route, including chat.

The schema is generated from FastAPI. This module only adds the parts the
framework cannot infer: tag groups, Bearer auth, and the production server
so Swagger UI's "Try it out" defaults to prod.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

DEFAULT_PRODUCTION_URL = "https://retailmind-ai-ten.vercel.app"

OPENAPI_TAGS = [
    {"name": "ops", "description": "Liveness and readiness. No auth."},
    {"name": "auth", "description": "Register, login, and current user. Login returns a Bearer JWT."},
    {
        "name": "chat",
        "description": (
            "Shopping / support assistant. POST `/api/chat` is the main turn. "
            "`/api/chat/stream` is the same run as SSE. Modes: auto, chat, rag, "
            "agent, multi_agent. Policy questions use RAG; checkout and orders use tools."
        ),
    },
    {"name": "shop", "description": "Catalogue and customer orders (REST, not the agent)."},
    {"name": "admin", "description": "Shop-owner catalogue, inventory, and order desk. Operator Bearer required."},
    {"name": "hitl", "description": "Human-in-the-loop approval queue for high-value tool calls."},
    {"name": "memory", "description": "Per-customer assistant memory (view / forget)."},
    {"name": "observability", "description": "Traces and agent runs for a completed chat turn."},
    {"name": "platform", "description": "Tool contracts and prompt versions."},
    {"name": "evaluation", "description": "Datasets, eval runs, and regression compare."},
    {"name": "benchmark", "description": "Model comparison runs."},
    {"name": "feedback", "description": "Thumbs / ratings on assistant answers."},
]


def production_base_url() -> str:
    """Stable public origin. Override with PUBLIC_BASE_URL."""
    try:
        from app.config import get_settings

        configured = (get_settings().public_base_url or "").strip().rstrip("/")
        if configured:
            return configured if configured.startswith("http") else f"https://{configured}"
    except Exception:  # noqa: BLE001 — schema generation must not fail import
        pass
    for key in ("PUBLIC_BASE_URL", "PUBLIC_URL"):
        raw = (os.environ.get(key) or "").strip().rstrip("/")
        if raw:
            return raw if raw.startswith("http") else f"https://{raw}"
    vercel_prod = (os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") or "").strip().rstrip("/")
    if vercel_prod:
        return vercel_prod if vercel_prod.startswith("http") else f"https://{vercel_prod}"
    return DEFAULT_PRODUCTION_URL


def openapi_servers() -> list[dict[str, str]]:
    prod = production_base_url()
    servers = [{"url": prod, "description": "Production"}]
    local = "http://127.0.0.1:8000"
    if prod.rstrip("/") not in {local, "http://localhost:8000"}:
        servers.append({"url": local, "description": "Local (make dev)"})
        servers.append({"url": "http://localhost:8000", "description": "Local (localhost)"})
    return servers


def build_openapi(app: FastAPI) -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=OPENAPI_TAGS,
        servers=openapi_servers(),
    )
    schema["info"]["contact"] = {"name": "RetailMind AI"}
    schema.setdefault("components", {})
    schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT or demo",
            "description": (
                "After `POST /api/auth/login`, paste the `access_token`. "
                "Demo tokens also work: `customer:CU-1001` (shopper) or "
                "`operator:op-1` (shop owner). Guests may omit the header."
            ),
        }
    }
    schema["security"] = [{"BearerAuth": []}]
    app.openapi_schema = schema
    return schema
