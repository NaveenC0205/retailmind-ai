"""Hybrid retrieval: vector + BM25, fused with RRF, reranked, trust-filtered.

Local profile runs BM25 in-process over the chunk table. The docker profile
puts the same corpus in OpenSearch. Scores differ slightly; the *evaluation*
does not care, because it asserts on chunk ids and ranks, not on raw scores.
"""
from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from app.config import get_settings
from app.models import KBChunk, KBDocument
from app.rag.embed import cosine, get_embedder, tokenize
from app.tracing import span

TRUST_ORDER = {"untrusted": 0, "semi": 1, "trusted": 2}


@dataclass
class Hit:
    chunk_id: str
    document_id: str
    content: str
    heading: str
    trust: str
    family: str
    score: float = 0.0
    vector_rank: Optional[int] = None
    lexical_rank: Optional[int] = None
    rerank_score: float = 0.0
    meta: dict = field(default_factory=dict)


class BM25:
    """Okapi BM25 over an in-memory corpus."""

    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.corpus = corpus
        self.n = len(corpus)
        self.doc_len = [len(d) for d in corpus]
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0
        self.tf: list[Counter] = [Counter(d) for d in corpus]
        df: Counter = Counter()
        for d in corpus:
            for term in set(d):
                df[term] += 1
        self.idf = {
            t: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()
        }

    def score(self, query: list[str]) -> list[float]:
        out = [0.0] * self.n
        for i in range(self.n):
            tf = self.tf[i]
            dl = self.doc_len[i] or 1
            s = 0.0
            for term in query:
                f = tf.get(term, 0)
                if not f:
                    continue
                idf = self.idf.get(term, 0.0)
                s += idf * (f * (self.k1 + 1)) / (
                    f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                )
            out[i] = s
        return out


def reciprocal_rank_fusion(rank_lists: list[list[str]], k: int = 60) -> dict[str, float]:
    """RRF. Rank-based, so it fuses scores from systems on different scales
    without any normalisation guesswork -- which is why it beats hand-tuned
    score blending in practice."""
    fused: dict[str, float] = {}
    for ranks in rank_lists:
        for pos, doc_id in enumerate(ranks):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + pos + 1)
    return fused


STOPWORDS = frozenset(
    """a an and are as at be been but by can could did do does doing for from had has have
    how i if in into is it its me my of on or our so than that the their them then there
    these they this to us was we were what when where which who why will with would you
    your please tell show give get need want know about""".split()
)


def content_terms(text: str) -> list[str]:
    return [t for t in tokenize(text) if t not in STOPWORDS and len(t) > 2]


def rerank(
    query: str,
    hits: list[Hit],
    idf: Optional[dict] = None,
    expanded: Optional[list[str]] = None,
    expansion_weight: float = 0.35,
) -> list[Hit]:
    """Stand-in cross-encoder: IDF-weighted coverage of the query's content
    terms, plus a heading-match bonus.

    A real deployment loads a cross-encoder here. This is cheap, deterministic
    and reproduces the *shape* of reranking (precision at the very top
    improves), so the rerank-lift metric is meaningful. Swap the body of this
    function and nothing else changes.

    The IDF weighting is not a nicety. Unweighted term coverage lets a query
    like "Do you ship to Singapore?" match the privacy policy on the words
    "you" and "your" -- the retrieved chunk is irrelevant but scores well, the
    model answers from it, and you have manufactured a hallucination.
    """
    q_terms = content_terms(query)
    if not q_terms:
        return hits
    idf = idf or {}
    weights = {t: idf.get(t, 1.0) for t in q_terms}
    # Expanded terms are a hint, not evidence. Giving them full weight is how
    # "what is the warranty on a laptop?" gets answered from the RETURN policy:
    # the expansion "laptop -> laptops" matches the returns chunk twice over and
    # outvotes the one term that actually carries the question, "warranty".
    for t in (expanded or []):
        if t not in weights:
            weights[t] = expansion_weight * idf.get(t, 1.0)
    total = sum(weights.values()) or 1.0
    for h in hits:
        body = set(content_terms(h.content))
        head = set(content_terms(h.heading))
        covered = sum(w for t, w in weights.items() if t in body)
        head_cov = sum(w for t, w in weights.items() if t in head)
        score = (covered / total) + 0.35 * (head_cov / total)
        # Store-policy questions should prefer the authoritative policy over a
        # catalogue description that happens to mention the same product type.
        policy_terms = {"warranty", "return", "returns", "refund", "shipping", "privacy"}
        if policy_terms.intersection(q_terms) and h.family.endswith("_policy") and h.trust == "trusted":
            if policy_terms.intersection(q_terms).intersection(body | head):
                score += 0.35
        h.rerank_score = round(score, 6)
    return sorted(hits, key=lambda h: (h.rerank_score, h.score), reverse=True)


# A small domain synonym map. Query expansion is worth its complexity only if
# you measure the lift -- tests/rag/test_retrieval_quality.py does exactly that,
# and if the lift ever goes negative this map should be deleted, not tuned.
SYNONYMS: dict[str, list[str]] = {
    # Morphological and true-synonym pairs only. Category *generalisations*
    # ("laptop" -> "electronics") were removed after measuring the lift: they
    # pulled specific questions onto whichever chunk happened to enumerate the
    # category. See tests/rag/test_retrieval_quality.py::test_expansion_lift.
    "clothes": ["apparel"],
    "clothing": ["apparel"],
    "shirt": ["apparel"],
    "laptop": ["laptops"],
    "phone": ["phones"],
    "headphones": ["audio"],
    "headphone": ["audio"],
    "earbuds": ["audio"],
    "monitor": ["monitors"],
    "delivery": ["delivered"],
    "deliver": ["delivered"],
    "money": ["refund"],
    "cancel": ["cancelled", "cancellation"],
    "coupon": ["coupons"],
    "discount": ["coupon"],
    "guarantee": ["warranty"],
    "covered": ["warranty"],
    "cover": ["warranty"],
    "broken": ["damaged", "defect"],
    "late": ["delayed", "delay"],
    "delayed": ["delay"],
}


def expansion_terms(query: str) -> list[str]:
    extra: list[str] = []
    original = set(content_terms(query))
    for term in original:
        extra.extend(t for t in SYNONYMS.get(term, []) if t not in original)
    return list(dict.fromkeys(extra))


def expand_query(query: str) -> str:
    extra = expansion_terms(query)
    return query + (" " + " ".join(extra) if extra else "")


class Retriever:
    def __init__(self, session):
        self.session = session
        self.embedder = get_embedder()

    async def _load(
        self, as_of: Optional[datetime], include_superseded: bool = False
    ) -> list[tuple[KBChunk, KBDocument]]:
        rows = (
            await self.session.execute(
                select(KBChunk, KBDocument).join(KBDocument, KBChunk.document_id == KBDocument.id)
            )
        ).all()
        if include_superseded:
            # Audit path: every version, in force or not. Used by compliance
            # questions ("what did the policy say in March?") and by the
            # stale-policy tests.
            return [(c, d) for c, d in rows]
        as_of = as_of or datetime.utcnow()
        live: list[tuple[KBChunk, KBDocument]] = []
        for chunk, doc in rows:
            if doc.effective_from and doc.effective_from > as_of:
                continue
            if doc.effective_to and doc.effective_to <= as_of:
                continue
            live.append((chunk, doc))
        return live

    async def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        rerank_k: Optional[int] = None,
        min_trust: str = "untrusted",
        families: Optional[list[str]] = None,
        as_of: Optional[datetime] = None,
        include_superseded: bool = False,
        min_score: Optional[float] = None,
        expand: bool = True,
    ) -> list[Hit]:
        s = get_settings()
        top_k = top_k or s.retrieval_top_k
        rerank_k = rerank_k or s.rerank_top_k
        min_score = s.retrieval_min_score if min_score is None else min_score
        started = time.perf_counter()

        with span("rag.retrieve", query=query[:200], strategy="hybrid") as sp:
            rows = await self._load(as_of, include_superseded=include_superseded)
            if families:
                rows = [(c, d) for c, d in rows if d.family in families]
            if not rows:
                sp.attributes.update({"chunk_ids": [], "reranked_ids": [], "hit_count": 0})
                return []

            chunks = [c for c, _ in rows]
            docs = {d.id: d for _, d in rows}
            extra_terms = expansion_terms(query) if expand else []
            search_query = query + ((" " + " ".join(extra_terms)) if extra_terms else "")
            sp.attributes["expansion_terms"] = extra_terms

            # IDF over the live corpus, used by the reranker.
            import math as _math
            df: dict[str, int] = {}
            for c in chunks:
                for t in set(content_terms(f"{c.heading} {c.content}")):
                    df[t] = df.get(t, 0) + 1
            n_docs = len(chunks) or 1
            idf = {t: _math.log(1 + n_docs / (v + 0.5)) for t, v in df.items()}

            # vector leg
            qvec = (await self.embedder.embed([search_query]))[0]
            vec_scores = [(c.id, cosine(qvec, c.embedding or [])) for c in chunks]
            vec_ranked = [cid for cid, _ in sorted(vec_scores, key=lambda x: x[1], reverse=True)][:top_k]

            # lexical leg
            corpus = [tokenize(f"{c.heading} {c.content}") for c in chunks]
            bm = BM25(corpus)
            lex_scores = bm.score(tokenize(search_query))
            lex_pairs = sorted(
                zip([c.id for c in chunks], lex_scores), key=lambda x: x[1], reverse=True
            )
            lex_ranked = [cid for cid, sc in lex_pairs if sc > 0][:top_k]

            fused = reciprocal_rank_fusion([vec_ranked, lex_ranked])
            by_id = {c.id: c for c in chunks}
            hits: list[Hit] = []
            for cid, score in sorted(fused.items(), key=lambda x: x[1], reverse=True)[:top_k]:
                c = by_id[cid]
                doc = docs[c.document_id]
                hits.append(
                    Hit(
                        chunk_id=c.id,
                        document_id=c.document_id,
                        content=c.content,
                        heading=c.heading,
                        trust=doc.source_trust,
                        family=doc.family,
                        score=round(score, 6),
                        vector_rank=vec_ranked.index(cid) if cid in vec_ranked else None,
                        lexical_rank=lex_ranked.index(cid) if cid in lex_ranked else None,
                        meta=c.meta or {},
                    )
                )

            pre_rerank_ids = [h.chunk_id for h in hits]
            hits = rerank(query, hits, idf=idf, expanded=extra_terms)

            # Relevance floor. A chunk that shares almost no rare term with the
            # query is not an answer, and passing it to the model manufactures
            # a hallucination. Dropping it lets the model refuse honestly --
            # which is why unanswerable questions belong in the golden set.
            below_floor = [h.chunk_id for h in hits if h.rerank_score < min_score]
            hits = [h for h in hits if h.rerank_score >= min_score]

            # trust filter -- the load-bearing RAG-poisoning defence
            required = TRUST_ORDER.get(min_trust, 0)
            kept, dropped = [], []
            for h in hits:
                if TRUST_ORDER.get(h.trust, 0) >= required:
                    kept.append(h)
                else:
                    dropped.append(h.chunk_id)
            final = kept[:rerank_k]

            sp.attributes.update(
                {
                    "chunk_ids": pre_rerank_ids,
                    "reranked_ids": [h.chunk_id for h in final],
                    "dropped_untrusted": dropped,
                    "dropped_below_floor": below_floor[:10],
                    "min_trust": min_trust,
                    "min_score": min_score,
                    "hit_count": len(final),
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                }
            )
            return final
