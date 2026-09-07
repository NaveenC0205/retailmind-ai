"""The Tool Gateway is the policy enforcement point. These are its tests."""
import asyncio

import pytest

from app.tools.contracts import ToolContract, registry
from app.tools.gateway import CircuitBreaker, ToolGateway
from app.tools.impl import ToolFailure

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def gw(session, principal, **kw):
    return ToolGateway(session, principal, run_id="RUN-test", **kw)


# --- 1 exists ----------------------------------------------------------
async def test_unknown_tool_is_refused_with_the_available_list(session, naveen, trace):
    r = await (await gw(session, naveen)).call("delete_everything", {})
    assert r.verdict == "deny_unknown"
    assert "search_products" in r.denial_reason


# --- 2 schema ----------------------------------------------------------
async def test_missing_required_argument_is_refused_not_guessed(session, naveen, trace):
    r = await (await gw(session, naveen)).call("get_order", {})
    assert r.verdict == "deny_schema"
    assert "order_id" in r.denial_reason


async def test_out_of_range_argument_is_refused(session, naveen, trace):
    r = await (await gw(session, naveen)).call("search_products", {"limit": 9999})
    assert r.verdict == "deny_schema"


async def test_hallucinated_identifier_is_refused_at_ownership_not_executed(session, naveen, trace):
    r = await (await gw(session, naveen)).call("get_order", {"order_id": "OR-DOESNOTEXIST"})
    assert r.status == "denied"
    assert r.verdict == "deny_ownership"


# --- 3 scope -----------------------------------------------------------
async def test_guest_may_browse_the_catalogue(session, anon, trace):
    r = await (await gw(session, anon)).call("search_products", {"query": "laptop"})
    assert r.ok


async def test_guest_may_not_read_orders(session, anon, trace):
    r = await (await gw(session, anon)).call("get_orders", {"customer_id": "CU-1001"})
    assert r.verdict == "deny_scope"
    assert "orders:read" in r.denial_reason


# --- 4 ownership -------------------------------------------------------
async def test_reading_another_customers_order_is_refused(session, naveen, trace):
    r = await (await gw(session, naveen)).call("get_order", {"order_id": "OR-VICTIM01"})
    assert r.verdict == "deny_ownership"


async def test_denial_message_does_not_confirm_the_record_exists(session, naveen, trace):
    """An 'access denied' that differs from 'not found' is an enumeration
    oracle: an attacker learns which order ids are real."""
    existing = await (await gw(session, naveen)).call("get_order", {"order_id": "OR-VICTIM01"})
    absent = await (await gw(session, naveen)).call("get_order", {"order_id": "OR-NOPE0001"})
    assert existing.denial_reason == absent.denial_reason


async def test_cancelling_another_customers_order_is_refused(session, naveen, trace):
    r = await (await gw(session, naveen)).call("cancel_order", {"order_id": "OR-VICTIM01"})
    assert r.verdict == "deny_ownership"
    assert r.status == "denied"


async def test_tracking_another_customers_shipment_is_refused(session, naveen, trace):
    r = await (await gw(session, naveen)).call("track_shipment", {"shipment_id": "SH-VICTIM01"})
    assert r.verdict == "deny_ownership"


async def test_reading_another_customers_payment_is_refused(session, naveen, trace):
    r = await (await gw(session, naveen)).call("get_refund", {"payment_id": "PY-VICTIM01"})
    assert r.verdict == "deny_ownership"


async def test_a_cross_customer_attempt_is_flagged_on_the_span(session, naveen, trace):
    await (await gw(session, naveen)).call("get_order", {"order_id": "OR-VICTIM01"})
    flagged = [s for s in trace.find("tool.call")
               if s.attributes.get("security.cross_customer_attempt")]
    assert flagged, "a cross-customer attempt must be visible in the trace"


# --- 5 HITL ------------------------------------------------------------
async def test_high_value_cancellation_suspends_for_a_human(session, naveen, trace):
    r = await (await gw(session, naveen)).call(
        "cancel_order", {"order_id": "OR-20004"},
        hitl_context={"order_total_inr": 92000},
    )
    assert r.status == "suspended"
    assert r.data["approval_id"].startswith("APR-")


async def test_a_suspended_call_does_not_execute(session, naveen, trace):
    """OR-20006 exists solely for this assertion, so the test does not depend
    on whether some other suite has already approved OR-20004."""
    from app.models import Order

    r = await (await gw(session, naveen)).call(
        "cancel_order", {"order_id": "OR-20006"}, hitl_context={"order_total_inr": 88000}
    )
    assert r.status == "suspended"
    order = await session.get(Order, "OR-20006")
    await session.refresh(order)
    assert order.status == "placed"


async def test_below_threshold_cancellation_runs_without_a_human(session, naveen, trace):
    r = await (await gw(session, naveen)).call(
        "cancel_order", {"order_id": "OR-20003"}, hitl_context={"order_total_inr": 12900}
    )
    assert r.ok
    assert r.data["cancelled"] is True


# --- 6 idempotency -----------------------------------------------------
async def test_repeating_a_write_replays_rather_than_repeating_it(session, naveen, trace):
    g = await gw(session, naveen)
    first = await g.call("create_support_ticket",
                         {"customer_id": "CU-1001", "category": "general", "summary": "x"})
    second = await g.call("create_support_ticket",
                          {"customer_id": "CU-1001", "category": "general", "summary": "x"})
    assert first.data["ticket_id"] == second.data["ticket_id"]


# --- 7/8 failure handling ---------------------------------------------
async def test_business_rule_failure_reaches_the_model_as_a_typed_message(session, naveen, trace):
    r = await (await gw(session, naveen)).call("get_product", {"product_id": "PR-NOPE"})
    assert r.status == "error"
    assert "No product with id" in r.denial_reason
    assert "Traceback" not in r.denial_reason


async def test_an_unexpected_exception_never_reaches_the_model(session, naveen, trace):
    """The model must never see a stack trace, a file path or a hostname --
    it will happily read them out to the customer."""
    async def explode(sess, principal, args):
        raise RuntimeError("psycopg2.OperationalError: could not connect to 10.0.0.4:5432")

    registry._tools["_boom"] = ToolContract(
        name="_boom", description="test only", input_model=registry.get("get_active_promotions").input_model,
        required_scopes=("products:read",), handler=explode, max_retries=0,
    )
    try:
        r = await (await gw(session, naveen)).call("_boom", {})
        assert r.status == "error"
        assert "psycopg2" not in r.denial_reason
        assert "10.0.0.4" not in r.denial_reason
        assert "temporarily unavailable" in r.denial_reason
        assert "psycopg2" not in str(r.for_model())
    finally:
        registry._tools.pop("_boom", None)


async def test_a_timeout_is_retried_then_reported(session, naveen, trace):
    calls = {"n": 0}

    async def slow(sess, principal, args):
        calls["n"] += 1
        await asyncio.sleep(0.5)
        return {}

    registry._tools["_slow"] = ToolContract(
        name="_slow", description="test only",
        input_model=registry.get("get_active_promotions").input_model,
        required_scopes=("products:read",), handler=slow, timeout_ms=30, max_retries=2,
    )
    try:
        r = await (await gw(session, naveen)).call("_slow", {})
        assert r.status == "error"
        assert r.retries == 2
        assert calls["n"] == 3
    finally:
        registry._tools.pop("_slow", None)


async def test_circuit_opens_after_repeated_failures_and_recovers():
    cb = CircuitBreaker(fail_threshold=3, window_s=30, open_s=0.05)
    for _ in range(3):
        cb.record_failure("t")
    assert cb.is_open("t")
    await asyncio.sleep(0.06)
    assert not cb.is_open("t")


async def test_secrets_are_redacted_from_the_span(session, naveen, trace):
    await (await gw(session, naveen)).call("validate_coupon", {"code": "SAVE10"})
    from app.tools.gateway import _safe_args

    assert _safe_args({"card_number": "4539578763621486"})["card_number"] == "[redacted]"
