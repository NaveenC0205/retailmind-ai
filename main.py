"""ASGI entrypoint for Vercel and other hosts.

Puts `backend/` on sys.path so `app.*` imports resolve, then re-exports the
FastAPI `app` instance Vercel / uvicorn expect.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.main import app  # noqa: E402

__all__ = ["app"]
