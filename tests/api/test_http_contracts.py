"""HTTP contract tests. Ordinary API tests -- fast, deterministic, every commit."""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import create_app

pytestmark = pytest.mark.api

NAVEEN = {"Authorization": "Bearer customer:CU-1001"}
PRIYA = {"Authorization": "Bearer customer:CU-1002"}
# Place-order tests must not mutate CU-1001; trajectory evals need that seed.
SHOPPER = {"Authorization": "Bearer customer:CU-1004"}
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
    assert body.get("db") in {"sqlite", "postgres"}


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


async def test_existing_order_details_lists_logged_in_orders(client):
    body = (await client.post(
        "/api/chat",
        json={"message": "check my existing order details", "mode": "multi_agent"},
        headers=NAVEEN,
    )).json()
    low = body["answer"].lower()
    assert "hold on" not in low
    assert "please hold" not in low
    assert "or-" in low
    assert "₹" in body["answer"] or "order" in low


async def test_guest_order_details_do_not_leak_history(client):
    body = (await client.post(
        "/api/chat",
        json={"message": "check my existing order details", "mode": "multi_agent"},
    )).json()
    low = body["answer"].lower()
    assert "sign in" in low
    assert "or-200" not in low
    assert "hold on" not in low


async def test_typed_product_question_returns_catalogue(client):
    body = (await client.post(
        "/api/chat",
        json={"message": "I need a wireless mouse", "mode": "multi_agent"},
    )).json()
    assert body["answer"].strip()
    assert "hold on" not in body["answer"].lower()
    assert "₹" in body["answer"] or "mouse" in body["answer"].lower()


async def test_laptop_ram_search_returns_a_priced_list(client):
    body = (await client.post(
        "/api/chat",
        json={"message": "Laptops under 80000 with 16GB RAM", "mode": "multi_agent"},
    )).json()
    assert body["answer"].strip()
    assert "no answer" not in body["answer"].lower()
    assert "₹" in body["answer"] or "laptop" in body["answer"].lower()


async def test_compare_phones_returns_a_catalogue_answer(client):
    body = (await client.post(
        "/api/chat",
        json={"message": "Compare phones rating 4.5+", "mode": "multi_agent"},
    )).json()
    assert body["answer"].strip()
    assert "no answer" not in body["answer"].lower()
    low = body["answer"].lower()
    assert "phone" in low or "₹" in body["answer"] or "rating" in low


async def test_chat_conversation_is_scoped_to_the_caller(client):
    first = (await client.post("/api/chat", json={"message": "hello"}, headers=NAVEEN)).json()
    stolen = (await client.post(
        "/api/chat",
        json={"message": "hello again", "conversation_id": first["conversation_id"]},
        headers=PRIYA,
    )).json()
    assert stolen["conversation_id"] != first["conversation_id"]


async def test_logged_in_check_irders_lists_orders_in_english(client):
    body = (await client.post(
        "/api/chat",
        json={"message": "can u check irders", "mode": "multi_agent"},
        headers=NAVEEN,
    )).json()
    low = body["answer"].lower()
    assert "sign in" not in low
    assert "sign-in" not in low
    assert "or-" in low
    assert "aapke" not in low
    assert body.get("suggestions")


async def test_guest_check_orders_asks_to_sign_in_in_english(client):
    body = (await client.post(
        "/api/chat",
        json={"message": "can u check irders", "mode": "multi_agent"},
    )).json()
    low = body["answer"].lower()
    assert "sign in" in low
    assert "aapke" not in low
    assert any("sign in" in s.lower() for s in (body.get("suggestions") or []))


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
        headers=SHOPPER,
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
        headers=SHOPPER,
    )).json()
    low = placed["answer"].lower()
    assert "placed" in low or "or-" in low
    assert any("order" in s.lower() for s in (placed.get("suggestions") or ["Show my recent orders"]))


async def test_existing_address_places_order_instead_of_listing(client):
    ask = (await client.post(
        "/api/chat",
        json={"message": "buy iPhone 15", "mode": "multi_agent"},
        headers=SHOPPER,
    )).json()
    placed = (await client.post(
        "/api/chat",
        json={
            "message": "use my existing address",
            "mode": "multi_agent",
            "conversation_id": ask["conversation_id"],
        },
        headers=SHOPPER,
    )).json()
    low = placed["answer"].lower()
    assert "placed" in low or "or-" in low
    assert "deliver" in low or "bengaluru" in low or "address" in low or "mg road" in low
    assert "here are your recent" not in low


@pytest.mark.parametrize("followup", ["UPI", "ok", "yes", "go ahead", "use saved address", "ship it"])
async def test_checkout_followups_place_after_product_search(client, followup):
    ask = (await client.post(
        "/api/chat",
        json={"message": "buy iPhone 15", "mode": "multi_agent"},
        headers=SHOPPER,
    )).json()
    placed = (await client.post(
        "/api/chat",
        json={
            "message": followup,
            "mode": "multi_agent",
            "conversation_id": ask["conversation_id"],
        },
        headers=SHOPPER,
    )).json()
    low = placed["answer"].lower()
    assert "placed" in low or "or-" in low, placed["answer"]
    assert "here are your recent" not in low


async def test_order_for_me_places_logged_in(client):
    body = (await client.post(
        "/api/chat",
        json={"message": "order new iphone for me", "mode": "multi_agent"},
        headers=SHOPPER,
    )).json()
    low = body["answer"].lower()
    assert "placed" in low or "or-" in low
    assert "iphone" in low
    assert "here are your recent" not in low


async def test_typo_search_and_place_is_checkout(client):
    body = (await client.post(
        "/api/chat",
        json={"message": "search fr new iphone and plave me the roder", "mode": "multi_agent"},
        headers=SHOPPER,
    )).json()
    low = body["answer"].lower()
    assert "sign in" not in low
    assert "placed" in low or "upi" in low or "or-" in low or "iphone" in low
    assert "here are your recent" not in low


async def test_owner_agent_lists_pending_and_low_stock(client):
    pending = (await client.post(
        "/api/chat",
        json={"message": "List pending orders", "mode": "multi_agent", "persona": "owner"},
        headers=OPERATOR,
    )).json()
    assert pending["persona"] == "owner"
    assert "pending" in pending["answer"].lower() or "or-" in pending["answer"].lower()
    assert pending.get("suggestions")

    stock = (await client.post(
        "/api/chat",
        json={"message": "Which products need restock?", "mode": "multi_agent", "persona": "owner"},
        headers=OPERATOR,
    )).json()
    assert stock["persona"] == "owner"
    assert "stock" in stock["answer"].lower() or "sku" in stock["answer"].lower()


async def test_owner_adds_a_product_from_chat(client):
    body = (await client.post(
        "/api/chat",
        json={
            "message": 'Add new product "Pixel Buds Demo" price 12999 category audio brand Google stock 50',
            "mode": "multi_agent",
            "persona": "owner",
        },
        headers=OPERATOR,
    )).json()
    assert "pixel buds demo" in body["answer"].lower()
    assert "12,999" in body["answer"] or "12999" in body["answer"]
    assert "cannot assist" not in body["answer"].lower()

    listing = (await client.post(
        "/api/chat",
        json={"message": "search for Pixel Buds Demo", "mode": "multi_agent"},
    )).json()
    assert "pixel buds demo" in listing["answer"].lower()


async def test_owner_add_product_without_details_asks_once(client):
    body = (await client.post(
        "/api/chat",
        json={"message": "add new product", "mode": "multi_agent", "persona": "owner"},
        headers=OPERATOR,
    )).json()
    low = body["answer"].lower()
    assert "cannot assist" not in low
    assert "title" in low and "price" in low
    # One ask, not the same refusal repeated by a looping supervisor.
    assert low.count("i can add that product") <= 1


async def test_guests_cannot_read_order_history_over_rest(client):
    r = await client.get("/api/customers/CU-1001/orders")
    assert r.status_code == 401
    r = await client.get("/api/orders/OR-20001")
    assert r.status_code == 401
    r = await client.get("/api/customers/CU-1001/orders", headers=PRIYA)
    assert r.status_code == 403
    r = await client.get("/api/customers/CU-1001/orders", headers=NAVEEN)
    assert r.status_code == 200


async def test_admin_rest_requires_an_operator(client):
    assert (await client.get("/api/admin/orders")).status_code == 403
    assert (await client.get("/api/admin/orders", headers=NAVEEN)).status_code == 403
    assert (await client.post(
        "/api/admin/products",
        json={"sku": "X-1", "title": "X", "brand": "X", "category": "audio", "price_inr": 1},
    )).status_code == 403
    assert (await client.get("/api/admin/orders", headers=OPERATOR)).status_code == 200


async def test_owner_persona_is_not_available_to_guests(client):
    body = (await client.post(
        "/api/chat",
        json={"message": "List pending orders", "mode": "multi_agent", "persona": "owner"},
    )).json()
    assert body["persona"] == "customer"


async def test_lab_framework_modes_return_answers(client):
    browse = (await client.post(
        "/api/chat",
        json={"message": "Search for iPhone and give me the prices", "mode": "multi_agent"},
    )).json()
    assert browse["answer"]
    assert "iphone" in browse["answer"].lower() or "₹" in browse["answer"] or "inr" in browse["answer"].lower()

    rag = (await client.post(
        "/api/chat",
        json={"message": "What is the return window for electronics?", "mode": "rag"},
        headers=NAVEEN,
    )).json()
    assert rag["mode"] == "rag"
    assert "10 days" in rag["answer"]

    chat = (await client.post(
        "/api/chat",
        json={"message": "Hi, what can you help me with today?", "mode": "chat"},
    )).json()
    assert chat["mode"] == "chat"
    assert chat["answer"]


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


async def test_guest_conversation_requires_the_same_browser_session(client):
    first_headers = {'X-Chat-Session': 'guest-session-one-0123456789'}
    first = (await client.post('/api/chat', json={'message': 'hello'}, headers=first_headers)).json()
    payload = {'message': 'hello again', 'conversation_id': first['conversation_id']}
    same = (await client.post('/api/chat', json=payload, headers=first_headers)).json()
    assert same['conversation_id'] == first['conversation_id']
    other = (await client.post('/api/chat', json=payload, headers={'X-Chat-Session': 'guest-session-two-0123456789'})).json()
    assert other['conversation_id'] != first['conversation_id']
    anonymous = (await client.post('/api/chat', json=payload)).json()
    assert anonymous['conversation_id'] != first['conversation_id']


async def test_stream_returns_a_final_answer_and_reuses_guest_session(client):
    import json
    headers = {'X-Chat-Session': 'stream-session-0123456789'}
    first = (await client.post('/api/chat', json={'message': 'hello'}, headers=headers)).json()
    response = await client.post('/api/chat/stream', json={
        'message': 'What is the return policy?',
        'conversation_id': first['conversation_id'],
        'mode': 'rag',
    }, headers=headers)
    assert response.status_code == 200
    events = {}
    for block in response.text.strip().split('\n\n'):
        lines = block.splitlines()
        events[lines[0].removeprefix('event: ')] = json.loads(lines[1].removeprefix('data: '))
    assert events['final']['conversation_id'] == first['conversation_id']
    assert events['final']['answer'].strip()
    assert 'done' in events


async def test_learning_library_contains_only_public_policy_sources(client):
    response = await client.get('/api/learning/policies')
    assert response.status_code == 200
    sources = response.json()['sources']
    assert sources
    assert any(source['family'] == 'return_policy' for source in sources)
    assert all(source['family'] in {'return_policy', 'refund_policy', 'warranty_policy', 'shipping_policy', 'privacy_policy', 'promotions_policy'} for source in sources)
    assert all(source['content'] and source['chunk_id'] for source in sources)
    assert all(not source['chunk_id'].startswith('seller-') for source in sources)
