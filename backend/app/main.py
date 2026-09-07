"""FastAPI application assembly."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.middleware import GatewayMiddleware
from app.api.routes import router
from app.config import get_settings
from app.db import create_all, dispose, session_scope

log = logging.getLogger("retailmind")


async def _bootstrap_if_empty() -> None:
    """Seed and ingest on first boot.

    A container starts with an empty volume, and an empty deployment is a
    broken demo. This is idempotent by inspection -- it only runs when the
    catalogue is empty -- so a restart with a persistent disk is a no-op.
    """
    from sqlalchemy import func, select

    from app.models import KBChunk, Product
    from app.rag.ingest import ingest_kb
    from app.seed import seed

    async with session_scope() as session:
        products = (await session.execute(select(func.count()).select_from(Product))).scalar() or 0
        chunks = (await session.execute(select(func.count()).select_from(KBChunk))).scalar() or 0
    if products and chunks:
        return
    log.info("empty database detected, seeding")
    async with session_scope() as session:
        await seed(session)
    async with session_scope() as session:
        await ingest_kb(session)
    log.info("bootstrap complete")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_all()
    if get_settings().auto_bootstrap:
        try:
            await _bootstrap_if_empty()
        except Exception as exc:  # noqa: BLE001 - never fail to start over seeding
            log.warning("auto-bootstrap skipped: %s", exc)
    log.info("retailmind ready")
    yield
    await dispose()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title=s.app_name,
        version="0.1.0",
        description=(
            "Enterprise agentic AI retail platform, built as an environment for "
            "AI evaluation and test engineering."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if s.rate_limit_per_min or s.daily_request_limit or s.access_password:
        app.add_middleware(
            GatewayMiddleware,
            rate_per_min=s.rate_limit_per_min or 10_000,
            burst=s.rate_limit_burst,
            daily_limit=s.daily_request_limit,
            access_password=s.access_password,
        )
    app.include_router(router)

    if s.static_dir.exists():
        shop_dir = s.static_dir / "shop"
        if shop_dir.exists():
            app.mount("/shop", StaticFiles(directory=str(shop_dir), html=True), name="shop")

            @app.get("/", include_in_schema=False)
            async def index():
                from fastapi.responses import RedirectResponse
                return RedirectResponse(url="/shop/", status_code=302)

    return app


app = create_app()
