"""Latency and concurrency, against the mocked tier.

These numbers are about the platform's own overhead -- routing, guardrails,
context assembly, the gateway, tracing -- with the model held constant at
zero. That is the number you can actually act on: when p95 moves in
production you need to know whether the model got slower or your code did.
"""
import asyncio
import statistics
import time

import pytest

from app.agents.orchestrator import Orchestrator
from app.db import session_scope
from app.security import customer
from app.tracing import new_trace, set_trace

pytestmark = pytest.mark.performance

QUERIES = [
    "What is the return window for electronics?",
    "Where is my order OR-20001?",
    "Find me a laptop under 80000",
    "What offers are running?",
]


async def one(text):
    new_trace()
    started = time.perf_counter()
    async with session_scope() as s:
        await Orchestrator(s, customer("CU-1001"), persist=False).run(text)
    set_trace(None)
    return (time.perf_counter() - started) * 1000


def percentile(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(p * len(values)))]


async def test_platform_overhead_stays_within_budget():
    latencies = [await one(QUERIES[i % len(QUERIES)]) for i in range(24)]
    p50, p95 = percentile(latencies, 0.5), percentile(latencies, 0.95)
    assert p95 < 3000, f"p50={p50:.0f}ms p95={p95:.0f}ms"


async def test_twenty_concurrent_conversations_all_succeed():
    started = time.perf_counter()
    results = await asyncio.gather(
        *[one(QUERIES[i % len(QUERIES)]) for i in range(20)], return_exceptions=True
    )
    elapsed = time.perf_counter() - started
    errors = [r for r in results if isinstance(r, Exception)]
    assert not errors, errors[:3]
    assert elapsed < 30, f"20 concurrent conversations took {elapsed:.1f}s"


async def test_retrieval_latency_is_recorded_and_bounded():
    trace = new_trace()
    async with session_scope() as s:
        await Orchestrator(s, customer("CU-1001"), persist=False).run(
            "What is the return window for electronics?"
        )
    span = trace.first("rag.retrieve")
    set_trace(None)
    assert span is not None
    assert span.duration_ms < 2000


async def test_a_multi_agent_run_costs_more_steps_than_a_single_agent_one():
    """Worth measuring rather than assuming: multi-agent buys coverage and
    pays in steps, tokens and money. If the ratio is not worth it for your
    traffic, route fewer requests there."""
    new_trace()
    async with session_scope() as s:
        single = await Orchestrator(s, customer("CU-1001"), persist=False).run(
            "Where is my order OR-20001?"
        )
    set_trace(None)
    new_trace()
    async with session_scope() as s:
        multi = await Orchestrator(s, customer("CU-1001"), persist=False).run(
            "My order is delayed, why, can I refund, raise a ticket"
        )
    set_trace(None)
    assert multi.steps > single.steps
    assert multi.tokens_in > single.tokens_in
