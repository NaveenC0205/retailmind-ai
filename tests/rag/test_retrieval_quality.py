"""Retrieval and generation, scored separately.

A single end-to-end accuracy number tells you nothing actionable: retrieval
and generation fail in different ways and need different fixes.
"""
import pytest

from app.db import session_scope
from app.evaluation import datasets as ds_mod
from app.evaluation.evaluators import mrr, ndcg_at_k, precision_at_k, recall_at_k
from app.rag.ingest import chunk_markdown
from app.rag.retrieve import (
    BM25,
    Retriever,
    content_terms,
    expansion_terms,
    reciprocal_rank_fusion,
    rerank,
)
from app.tracing import new_trace, set_trace

pytestmark = pytest.mark.rag

DATASET = ds_mod.load("rag/policy_qa")


async def search(query, **kw):
    trace = new_trace()
    async with session_scope() as s:
        hits = await Retriever(s).search(query, **kw)
    set_trace(None)
    return hits


# --- metric primitives -------------------------------------------------
def test_retrieval_metrics_are_correct():
    retrieved = ["a", "b", "c", "d", "e"]
    assert recall_at_k(retrieved, ["a", "z"], k=5) == 0.5
    assert precision_at_k(retrieved, ["a", "b"], k=5) == 0.4
    assert mrr(retrieved, ["c"]) == pytest.approx(1 / 3)
    assert ndcg_at_k(retrieved, ["a"], k=5) == 1.0


def test_rrf_prefers_a_document_both_systems_rank_well():
    fused = reciprocal_rank_fusion([["x", "y", "z"], ["y", "x", "w"]])
    assert fused["y"] > fused["z"]
    assert fused["x"] > fused["w"]


def test_bm25_rewards_rare_terms():
    corpus = [["refund", "policy"], ["shipping", "policy"], ["policy", "policy"]]
    scores = BM25(corpus).score(["refund"])
    assert scores[0] > scores[1] and scores[0] > scores[2]


# --- chunking ----------------------------------------------------------
def test_chunking_keeps_a_clause_with_its_heading():
    """A chunk that reads '10 days' with no heading is retrievable and useless."""
    body = "# Return window\n\nElectronics: 10 days.\n\n# Refunds\n\nCards: 5-7 days."
    chunks = chunk_markdown(body)
    headings = [h for h, _ in chunks]
    assert "Return window" in headings and "Refunds" in headings
    window = next(c for h, c in chunks if h == "Return window")
    assert "10 days" in window
    assert "Cards" not in window


# --- ranking behaviour -------------------------------------------------
async def test_stopwords_do_not_drive_the_ranking():
    """Unweighted term coverage lets 'Do you ship to Singapore?' match the
    privacy policy on the words 'you' and 'your'. That is a manufactured
    hallucination, and IDF weighting is what prevents it."""
    hits = await search("Do you ship to Singapore?")
    assert all(h.family != "privacy_policy" for h in hits[:1]), [h.chunk_id for h in hits[:3]]


async def test_expansion_helps_a_vocabulary_mismatch():
    """The corpus says 'apparel'; the customer says 'clothes'."""
    assert "apparel" in expansion_terms("How long do I have to return clothes?")
    hits = await search("How long do I have to return clothes?")
    assert hits and hits[0].family == "return_policy"


async def test_expansion_terms_are_weighted_below_original_terms():
    """Measured, not assumed. At full weight, 'laptop -> laptops' outvotes the
    one term that carries the question ('warranty') and the answer comes from
    the RETURN policy. This test is why SYNONYMS holds no category
    generalisations."""
    hits = await search("What is the warranty on a laptop?")
    assert hits[0].family == "warranty_policy", [(h.chunk_id, h.rerank_score) for h in hits[:3]]


async def test_reranking_improves_precision_at_the_top():
    query = "What is the return window for electronics?"
    trace = new_trace()
    async with session_scope() as s:
        hits = await Retriever(s).search(query, min_trust="trusted")
    span = trace.first("rag.retrieve")
    set_trace(None)
    pre = span.attributes["chunk_ids"]
    post = span.attributes["reranked_ids"]
    gold = "return_policy_v4-c00"
    assert post[0] == gold
    assert post.index(gold) <= pre.index(gold)


async def test_the_relevance_floor_drops_weak_matches():
    trace = new_trace()
    async with session_scope() as s:
        await Retriever(s).search("What is the return window for electronics?", min_trust="trusted")
    span = trace.first("rag.retrieve")
    set_trace(None)
    assert "dropped_below_floor" in span.attributes


# --- versioning --------------------------------------------------------
async def test_the_superseded_policy_is_never_retrieved():
    """return_policy_v3 says 7 days and expired in April 2026. Answering
    correctly from a superseded document is a production failure you cannot
    test for unless the corpus has a version axis."""
    hits = await search("What is the return window for electronics?", min_trust="trusted")
    assert all(h.document_id != "return_policy_v3" for h in hits)
    assert any(h.document_id == "return_policy_v4" for h in hits)


async def test_the_superseded_policy_is_still_reachable_for_an_audit():
    hits = await search("return window electronics 7 days", include_superseded=True,
                        min_trust="trusted", rerank_k=20)
    assert any(h.document_id == "return_policy_v3" for h in hits)


# --- trust -------------------------------------------------------------
async def test_policy_retrieval_excludes_untrusted_seller_copy():
    hits = await search("unconditional 365-day return window refunds for everyone",
                        min_trust="trusted")
    assert all(h.trust == "trusted" for h in hits)
    assert not any(h.chunk_id.startswith("seller-") for h in hits)


async def test_seller_copy_is_still_indexed_so_product_questions_work():
    hits = await search("Zenline 14", min_trust="untrusted")
    assert any(h.chunk_id.startswith("seller-") for h in hits)


async def test_dropped_untrusted_chunks_are_recorded_on_the_span():
    trace = new_trace()
    async with session_scope() as s:
        await Retriever(s).search("Swiftbook 15 return window", min_trust="trusted")
    span = trace.first("rag.retrieve")
    set_trace(None)
    assert "dropped_untrusted" in span.attributes


# --- corpus-level quality ---------------------------------------------
@pytest.mark.parametrize(
    "item",
    [i for i in DATASET.items if i["expected"].get("sources") and "known_gap" not in i],
    ids=lambda i: i["id"],
)
async def test_gold_document_is_retrieved(item):
    hits = await search(item["input"]["text"], min_trust="trusted")
    docs = {h.document_id for h in hits}
    assert set(item["expected"]["sources"]) & docs, (
        f"expected {item['expected']['sources']}, got {sorted(docs)}"
    )


def test_known_gaps_are_documented_rather_than_silently_failing():
    """Items the current retrieval stack cannot answer are marked with a
    diagnosis. An undocumented failing item is a bug; a documented one is a
    backlog entry."""
    gaps = [i for i in DATASET.items if "known_gap" in i]
    assert gaps, "if the gaps are fixed, delete this test"
    for item in gaps:
        assert len(item["known_gap"]) > 80, f"{item['id']}: gap note is not a diagnosis"


def test_the_dataset_is_structurally_valid():
    assert ds_mod.validate(DATASET) == []
