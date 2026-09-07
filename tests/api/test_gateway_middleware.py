"""Edge protections for a public URL.

None of this matters on a laptop. On a shared URL the cost ceiling is what
stops a stranger turning your API key into a bill overnight.
"""
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.middleware import Bucket, GatewayMiddleware, Meter

pytestmark = pytest.mark.api


def build(**kw) -> FastAPI:
    app = FastAPI()
    app.add_middleware(GatewayMiddleware, **kw)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/api/chat")
    async def chat():
        return {"answer": "hi"}

    @app.get("/api/tools")
    async def tools():
        return {"count": 22}

    return app


@pytest_asyncio.fixture
async def client_factory():
    clients = []

    async def make(**kw):
        c = AsyncClient(transport=ASGITransport(app=build(**kw)), base_url="http://t")
        clients.append(c)
        return c

    yield make
    for c in clients:
        await c.aclose()


# --- rate limiting -----------------------------------------------------
async def test_requests_under_the_limit_pass(client_factory):
    c = await client_factory(rate_per_min=600, burst=5)
    for _ in range(5):
        assert (await c.get("/api/tools")).status_code == 200


async def test_a_burst_beyond_the_bucket_is_throttled(client_factory):
    c = await client_factory(rate_per_min=60, burst=3)
    codes = [(await c.get("/api/tools")).status_code for _ in range(6)]
    assert codes[:3] == [200, 200, 200]
    assert 429 in codes[3:]


async def test_a_throttled_response_tells_the_caller_when_to_retry(client_factory):
    c = await client_factory(rate_per_min=60, burst=1)
    await c.get("/api/tools")
    r = await c.get("/api/tools")
    assert r.status_code == 429
    assert r.headers["Retry-After"]
    assert "per minute" in r.json()["detail"]


async def test_health_is_never_throttled(client_factory):
    """A health check that can be rate limited will fail a deploy at exactly
    the moment traffic arrives."""
    c = await client_factory(rate_per_min=60, burst=1)
    for _ in range(10):
        assert (await c.get("/health")).status_code == 200


def test_the_bucket_refills_over_time():
    b = Bucket(tokens=0.0, updated=0.0)
    import time as _t

    b.updated = _t.monotonic() - 60  # a full minute has passed
    assert b.take(rate_per_min=60, burst=10)


# --- daily budget ------------------------------------------------------
async def test_metered_endpoints_stop_at_the_daily_limit(client_factory):
    c = await client_factory(rate_per_min=10_000, burst=1000, daily_limit=3)
    codes = [(await c.post("/api/chat")).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


async def test_unmetered_endpoints_are_not_charged_to_the_budget(client_factory):
    """Reading a trace is free; starting an agent run is not."""
    c = await client_factory(rate_per_min=10_000, burst=1000, daily_limit=2)
    for _ in range(10):
        assert (await c.get("/api/tools")).status_code == 200
    assert (await c.post("/api/chat")).status_code == 200


async def test_the_budget_response_says_when_it_resets(client_factory):
    c = await client_factory(rate_per_min=10_000, burst=1000, daily_limit=1)
    await c.post("/api/chat")
    r = await c.post("/api/chat")
    assert r.status_code == 429
    assert "resets" in r.json()["detail"]
    assert r.json()["budget"]["limit"] == 1


def test_the_meter_rolls_over_to_a_new_day():
    m = Meter(daily_limit=1)
    assert m.allow()
    assert not m.allow()
    m.day = "1999-01-01"
    assert m.allow(), "a new day must reset the count"


def test_a_zero_limit_means_unmetered():
    m = Meter(daily_limit=0)
    assert all(m.allow() for _ in range(100))


# --- access gate -------------------------------------------------------
async def test_a_password_protected_demo_refuses_strangers(client_factory):
    c = await client_factory(rate_per_min=10_000, burst=100, access_password="s3cret")
    assert (await c.get("/api/tools")).status_code == 401


async def test_the_password_is_accepted_as_a_header_or_a_query_parameter(client_factory):
    """The query parameter exists so a single shareable link works."""
    c = await client_factory(rate_per_min=10_000, burst=100, access_password="s3cret")
    assert (await c.get("/api/tools", headers={"X-Demo-Password": "s3cret"})).status_code == 200
    assert (await c.get("/api/tools?k=s3cret")).status_code == 200


async def test_the_password_gate_does_not_block_health_checks(client_factory):
    c = await client_factory(rate_per_min=10_000, burst=100, access_password="s3cret")
    assert (await c.get("/health")).status_code == 200


# --- proxy awareness ---------------------------------------------------
async def test_callers_are_distinguished_by_forwarded_address(client_factory):
    """Behind Render or Fly every request arrives from the proxy. Without
    X-Forwarded-For the whole internet shares one bucket and the first busy
    visitor locks out everyone else."""
    c = await client_factory(rate_per_min=60, burst=1)
    assert (await c.get("/api/tools", headers={"X-Forwarded-For": "1.1.1.1"})).status_code == 200
    assert (await c.get("/api/tools", headers={"X-Forwarded-For": "2.2.2.2"})).status_code == 200
    assert (await c.get("/api/tools", headers={"X-Forwarded-For": "1.1.1.1"})).status_code == 429
