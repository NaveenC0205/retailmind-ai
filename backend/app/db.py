"""Async engine + session factory."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.models import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _postgres_engine_kwargs(url: str) -> dict:
    """Vercel is IPv4 + serverless; Supabase transaction pooler needs no prepared statements."""
    kwargs: dict = {"echo": False, "future": True, "pool_pre_ping": True}
    pooled = ":6543" in url or "pooler.supabase" in url or "pgbouncer" in url.lower()
    serverless = bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))
    if serverless:
        kwargs["poolclass"] = NullPool
    else:
        kwargs["pool_size"] = 5
        kwargs["max_overflow"] = 5
    if pooled:
        kwargs["connect_args"] = {"statement_cache_size": 0}
    return kwargs


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        s = get_settings()
        kwargs: dict = {"echo": False, "future": True, "pool_pre_ping": True}
        if s.is_sqlite:
            kwargs["connect_args"] = {"check_same_thread": False}
        elif s.is_postgres:
            kwargs = _postgres_engine_kwargs(s.database_url)
        _engine = create_async_engine(s.database_url, **kwargs)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with session_scope() as session:
        yield session


async def create_all() -> None:
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_all() -> None:
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
