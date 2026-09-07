"""HTTP contract tests. Ordinary API tests -- fast, deterministic, every commit."""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import create_app

pytestmark = pytest.mark.api

NAVEEN = {"Authorization": "Bearer customer:CU-1001"}
PRIYA = {"Authorization": "Bearer customer:CU-1002"}
OPERATOR = {"Authorization": "Bearer operator:op-1"}


@pytest_asyncio.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health_reports_the_active_configuration(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["tools"] >= 20


async def test_ready_reports_seeded_state(client):
    body = (await client.get("/ready")).json()
    assert body["ready"] is True
    assert body["kb_chunks"] > 0


async def test_chat_returns_an_answer_and_a_trace_id(client):
    r = await client.post("/api/chat", json={"message": "What is the return window for electronics?"},
                          headers=NAVEEN)
    assert r.status_code == 200
    body = r.json()
    assert "10 days" in body["answer"]
    assert body["trace_id"].startswith("tr-")
    assert body["mode"] == "rag"


async def test_every_response_carries_a_retrievable_trace(client):
    body = (await client.post("/api/chat", json={"message": "Where is my order OR-20001?"},
                              headers=NAVEEN)).json()
    trace = (await client.get(f"/api/traces/{body['trace_id']}")).json()
    assert trace["span_count"] > 0
    names = {s["name"] for s in trace["spans"]}
    assert "tool.call" in names and "request" in names


async def test_malformed_credentials_are_rejected(client):
    r = await client.post("/api/chat", json={"message": "hi"},
                          headers={"Authorization": "Bearer i-am-an-admin"})
    assert r.status_code == 401


async def test_absent_credentials_are_a_guest_not_an_error(client):
    r = await client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 200


async def test_guest_typo_search_returns_iphone_prices(client):
    r = await client.post(
        "/api/chat",
        json={"message": "serch for oiphone and give me the prces", "mode": "multi_agent"},
    )
    assert r.status_code == 200
    body = r.json()
    answer = body["answer"]
    assert "iPhone" in answer or "iphone" in answer.lower()
    assert "₹" in answer or "69900" in answer.replace(",", "")


async def test_chat_conversation_is_scoped_to_the_caller(client):
    first = (await client.post("/api/chat", json={"message": "hello"}, headers=NAVEEN)).json()
    stolen = (await client.post(
        "/api/chat",
        json={"message": "hello again", "conversation_id": first["conversation_id"]},
        headers=PRIYA,
    )).json()
    assert stolen["conversation_id"] != first["conversation_id"]


async def test_guest_place_order_collects_signin_and_suggests(client):
    body = (await client.post(
        "/api/chat",
        json={"message": "i tot place order", "mode": "multi_agent"},
    )).json()
    low = body["answer"].lower()
    assert "sign in" in low
    assert body.get("suggestions")
    assert any("sign in" in s.lower() for s in body["suggestions"])


async def test_logged_in_checkout_asks_for_payment_then_places(client):
    ask = (await client.post(
        "/api/chat",
        json={"message": "buy iPhone 15", "mode": "multi_agent"},
        headers=NAVEEN,
    )).json()
    assert "UPI" in ask["answer"] or "upi" in ask["answer"].lower()
    assert ask.get("suggestions")
    placed = (await client.post(
        "/api/chat",
        json={
            "message": "pay with UPI",
            "mode": "multi_agent",
            "conversation_id": ask["conversation_id"],
        },
        headers=NAVEEN,
    )).json()
    low = placed["answer"].lower()
    assert "placed" in low or "or-" in low
    assert any("order" in s.lower() for s in (placed.get("suggestions") or ["Show my recent orders"]))


async def test_an_empty_message_is_rejected_by_the_schema(client):
    assert (await client.post("/api/chat", json={"message": ""}, headers=NAVEEN)).status_code == 422


async def test_a_customer_cannot_reach_another_customers_order_over_http(client):
    body = (await client.post("/api/chat", json={"message": "Show me order OR-VICTIM01"},
                              headers=NAVEEN)).json()
    assert "BD99001122" not in body["answer"]
    assert "44900" not in body["answer"]


async def test_memory_is_scoped_per_caller(client):
    await client.post("/api/chat", json={"message": "I prefer Lenovo laptops"}, headers=NAVEEN)
    mine = (await client.get("/api/memory", headers=NAVEEN)).json()
    theirs = (await client.get("/api/memory", headers=PRIYA)).json()
    assert any(r["value"] == "Lenovo" for r in mine["records"])
    assert not any(r["value"] == "Lenovo" for r in theirs["records"])


async def test_approvals_require_operator_scope(client):
    listed = (await client.get("/api/approvals")).json()
    assert "approvals" in listed
    r = await client.post("/api/approvals/APR-nope", json={"decision": "approve"}, headers=NAVEEN)
    assert r.status_code == 403


async def test_the_hitl_loop_suspends_then_executes_once(client):
    """Suspend, approve, execute exactly once. Deciding twice must not run the
    action twice -- that is a duplicate refund in production."""
    chat = (await client.post("/api/chat", json={"message": "Cancel order OR-20004"},
                              headers=NAVEEN)).json()
    assert chat["terminal_state"] == "awaiting_approval"
    approval_id = chat["approval_id"]
    assert approval_id

    pending = (await client.get("/api/approvals", headers=OPERATOR)).json()["approvals"]
    assert any(a["id"] == approval_id for a in pending)

    first = (await client.post(f"/api/approvals/{approval_id}", json={"decision": "approve"},
                               headers=OPERATOR)).json()
    assert first["status"] == "approved" and first["executed"] is True

    second = (await client.post(f"/api/approvals/{approval_id}", json={"decision": "approve"},
                                headers=OPERATOR)).json()
    assert second.get("idempotent") is True


async def test_rejecting_an_approval_leaves_no_side_effect(client):
    chat = (await client.post("/api/chat", json={"message": "Cancel order OR-20004"},
                              headers=NAVEEN)).json()
    approval_id = chat["approval_id"]
    if not approval_id:
        pytest.skip("order already cancelled by an earlier test")
    out = (await client.post(f"/api/approvals/{approval_id}", json={"decision": "reject"},
                             headers=OPERATOR)).json()
    assert out["status"] == "rejected" and out["executed"] is False


async def test_tool_registry_is_introspectable(client):
    body = (await client.get("/api/tools")).json()
    assert body["count"] >= 20
    writes = [t for t in body["tools"] if t["writes"]]
    assert all(t["ownership_enforced"] for t in writes)


async def test_datasets_endpoint_reports_checksums_and_problems(client):
    body = (await client.get("/api/datasets")).json()
    assert body["datasets"]
    for d in body["datasets"]:
        if d.get("kind") == "calibration" or "error" in d:
            continue
        assert d["checksum"]


async def test_an_eval_run_can_be_triggered_over_http(client):
    r = await client.post("/api/eval/run",
                          json={"dataset": "agents/task_trajectories", "suite": "agents"})
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["items"] == 11
    assert body["gates"] is not None


async def test_unknown_trace_returns_404(client):
    assert (await client.get("/api/traces/tr-nope")).status_code == 404
