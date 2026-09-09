"""OpenAPI catalog covers chat and every other HTTP surface."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app

pytestmark = pytest.mark.api

REQUIRED_PATHS = {
    "/health",
    "/ready",
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/me",
    "/api/chat",
    "/api/chat/stream",
    "/api/products",
    "/api/products/{product_id}",
    "/api/orders",
    "/api/orders/{order_id}",
    "/api/customers/{customer_id}/orders",
    "/api/admin/orders",
    "/api/admin/products",
    "/api/tools",
    "/api/eval/run",
    "/api/traces/{trace_id}",
}


@pytest.mark.asyncio
async def test_openapi_lists_chat_and_all_public_routes():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    paths = spec["paths"]
    missing = sorted(REQUIRED_PATHS - set(paths))
    assert missing == []
    assert "post" in paths["/api/chat"]
    assert paths["/api/chat"]["post"]["tags"] == ["chat"]
    servers = [s["url"] for s in spec["servers"]]
    assert servers[0].startswith("https://")
    assert "BearerAuth" in spec["components"]["securitySchemes"]


@pytest.mark.asyncio
async def test_swagger_ui_is_served():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        docs = await c.get("/docs")
        swagger = await c.get("/swagger", follow_redirects=False)
        redoc = await c.get("/redoc")
    assert docs.status_code == 200
    assert "swagger" in docs.text.lower()
    assert swagger.status_code in {307, 302}
    assert swagger.headers["location"] == "/docs"
    assert redoc.status_code == 200
