#!/usr/bin/env python3
"""Pin dataset gold labels from document level to chunk level.

Hand-written datasets name the gold DOCUMENT, because chunk ids depend on the
chunker and pinning them early makes every chunking change look like a
retrieval regression. Once the chunker settles, run this: it finds the chunk
containing each item's `expected.answer` and writes it into `expected.chunks`,
after which the retrieval evaluators score at the stricter chunk level
automatically.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select  # noqa: E402

from app.db import create_all, dispose, session_scope  # noqa: E402
from app.models import KBChunk  # noqa: E402
from app.rag.embed import tokenize  # noqa: E402


async def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "rag/policy_qa"
    path = Path(__file__).resolve().parents[1] / "datasets" / f"{target}.json"
    data = json.loads(path.read_text())

    await create_all()
    async with session_scope() as s:
        chunks = (await s.execute(select(KBChunk))).scalars().all()
    await dispose()

    resolved = 0
    for item in data["items"]:
        evidence = (item.get("expected") or {}).get("answer") or ""
        sources = set((item.get("expected") or {}).get("sources") or [])
        if not evidence or not sources:
            continue
        want = set(t for t in tokenize(evidence) if len(t) > 3)
        best, best_score = None, 0.0
        for c in chunks:
            if c.document_id not in sources:
                continue
            have = set(tokenize(c.content))
            score = len(want & have) / max(1, len(want))
            if score > best_score:
                best, best_score = c.id, score
        if best and best_score >= 0.6:
            item["expected"]["chunks"] = [best]
            resolved += 1
        else:
            print(f"  unresolved: {item['id']} (best {best_score:.2f})")

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"pinned {resolved}/{len(data['items'])} items to chunk level in {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
