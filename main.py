"""Vercel / ASGI entrypoint with fail-soft import diagnostics.

If the full app fails to import on Vercel, we still expose a FastAPI `app` that
returns the traceback as JSON instead of crashing the serverless function.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

_IMPORT_ERROR: str | None = None

try:
    from app.main import app  # noqa: F401
except Exception:  # noqa: BLE001 - must never crash the serverless worker on import
    _IMPORT_ERROR = traceback.format_exc()
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse

    app = FastAPI(title="ShopZone (import error)")

    @app.get("/health")
    async def health():
        return {"status": "import_error", "detail": _IMPORT_ERROR.splitlines()[-1:]}

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def boom(full_path: str = ""):
        return JSONResponse(
            status_code=500,
            content={
                "error": "application_failed_to_import",
                "traceback": _IMPORT_ERROR,
            },
        )

__all__ = ["app"]
