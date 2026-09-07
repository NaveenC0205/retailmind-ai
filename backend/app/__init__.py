"""Ensure `backend/` is on sys.path so `import app` works on Vercel.

When Vercel loads `backend.app.main:app`, the package root is the repo, not
`backend/`. Without this, `from app...` fails with ModuleNotFoundError and the
serverless function crashes on import.
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_backend = str(_BACKEND_DIR)
if _backend not in sys.path:
    sys.path.insert(0, _backend)
