"""Knowledge-base ingestion: parse, chunk, version, trust-label, embed.

Two things here are not decoration:

  VERSIONING  Documents carry effective_from / effective_to. return_policy v3
              and v4 coexist. Retrieval filters to the version in force.
              "Answered correctly from a superseded policy" is one of the most
              expensive production RAG failures and you cannot test for it if
              the corpus has no version axis.

  TRUST       Every document gets a source_trust level. Seller-authored product
              copy is ingested as `untrusted` and can never be cited as policy.
              This is what makes RAG poisoning a testable property.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select

from app.config import get_settings
from app.models import KBChunk, KBDocument, Product, new_id
from app.rag.embed import get_embedder

FRONT_MATTER = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _parse_front_matter(text: str) -> tuple[dict, str]:
    m = FRONT_MATTER.match(text)
    if not m:
        return {}, text
    meta: dict = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, text[m.end():]


def chunk_markdown(body: str, target_chars: int = 700) -> list[tuple[str, str]]:
    """Split on headings, then pack paragraphs up to target size.

    Heading-aware chunking exists so a policy clause never gets split from the
    heading that gives it meaning. A chunk that reads "10 days" with no heading
    is retrievable and useless.
    """
    sections: list[tuple[str, list[str]]] = []
    heading = ""
    buf: list[str] = []
    for line in body.splitlines():
        if line.startswith("#"):
            if buf:
                sections.append((heading, buf))
            heading = line.lstrip("#").strip()
            buf = []
        else:
            buf.append(line)
    if buf:
        sections.append((heading, buf))

    chunks: list[tuple[str, str]] = []
    for head, lines in sections:
        paras = [p.strip() for p in "\n".join(lines).split("\n\n") if p.strip()]
        current = ""
        for p in paras:
            candidate = (current + "\n\n" + p).strip() if current else p
            if len(candidate) > target_chars and current:
                chunks.append((head, current))
                current = p
            else:
                current = candidate
        if current:
            chunks.append((head, current))
    return [(h, c) for h, c in chunks if c.strip()]


async def ingest_kb(session, kb_dir: Path | None = None) -> dict:
    """(Re)build the knowledge base from kb/*.md. Idempotent."""
    s = get_settings()
    kb_dir = kb_dir or s.kb_dir
    embedder = get_embedder()

    await session.execute(delete(KBChunk))
    await session.execute(delete(KBDocument))

    docs = 0
    chunk_rows: list[KBChunk] = []
    for path in sorted(kb_dir.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        meta, body = _parse_front_matter(raw)
        family = meta.get("family", path.stem.split("_v")[0])
        version = int(meta.get("version", "1"))
        trust = meta.get("trust", "trusted")
        eff_from = _parse_date(meta.get("effective_from")) or datetime(2024, 1, 1, tzinfo=timezone.utc)
        eff_to = _parse_date(meta.get("effective_to"))

        doc_id = path.stem
        session.add(
            KBDocument(
                id=doc_id,
                family=family,
                filename=path.name,
                version=version,
                effective_from=eff_from.replace(tzinfo=None),
                effective_to=eff_to.replace(tzinfo=None) if eff_to else None,
                source_trust=trust,
                checksum=hashlib.sha256(raw.encode()).hexdigest()[:16],
            )
        )
        docs += 1
        for i, (heading, content) in enumerate(chunk_markdown(body)):
            chunk_rows.append(
                KBChunk(
                    id=f"{doc_id}-c{i:02d}",
                    document_id=doc_id,
                    ordinal=i,
                    page=1 + i // 4,
                    heading=heading,
                    content=content,
                    embedding=[],
                    meta={"family": family, "version": version, "trust": trust},
                )
            )

    # Seller-authored product copy joins the same index as UNTRUSTED. It is
    # retrievable (customers ask about product details) but the trust filter
    # stops it being cited as policy, and the injection detector scans it.
    products = (await session.execute(select(Product))).scalars().all()
    if products:
        session.add(
            KBDocument(
                id="seller_catalog",
                family="catalog",
                filename="seller_catalog",
                version=1,
                effective_from=datetime(2024, 1, 1),
                source_trust="untrusted",
                checksum="",
            )
        )
        docs += 1
        for i, p in enumerate(products):
            if not (p.description_raw or "").strip():
                continue
            chunk_rows.append(
                KBChunk(
                    id=f"seller-{p.id}",
                    document_id="seller_catalog",
                    ordinal=i,
                    page=1,
                    heading=p.title,
                    content=f"{p.title} ({p.brand}, {p.category}). {p.description_raw}",
                    embedding=[],
                    meta={"family": "catalog", "trust": "untrusted", "product_id": p.id},
                )
            )

    vectors = await embedder.embed([c.content for c in chunk_rows]) if chunk_rows else []
    for chunk, vec in zip(chunk_rows, vectors):
        chunk.embedding = vec
        chunk.meta = {**chunk.meta, "embedder": embedder.name}
        session.add(chunk)

    await session.flush()
    return {"documents": docs, "chunks": len(chunk_rows), "embedder": embedder.name}


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip()).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def default_effective_window() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    return now - timedelta(days=365), now + timedelta(days=365)
