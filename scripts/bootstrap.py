#!/usr/bin/env python3
"""Create tables, seed business data, ingest the knowledge base.

Idempotent: safe to re-run. This is what `make bootstrap` calls, and what the
test fixtures call against an in-memory database.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db import create_all, dispose, session_scope  # noqa: E402
from app.rag.ingest import ingest_kb  # noqa: E402
from app.seed import seed  # noqa: E402


async def main() -> int:
    await create_all()
    async with session_scope() as session:
        seeded = await seed(session)
    async with session_scope() as session:
        ingested = await ingest_kb(session)
    await dispose()
    print("seeded   ", ", ".join(f"{k}={v}" for k, v in seeded.items()))
    print("ingested ", ", ".join(f"{k}={v}" for k, v in ingested.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
