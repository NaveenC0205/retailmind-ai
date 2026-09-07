"""Shared fixtures.

Every test runs against its own SQLite file, seeded and ingested once per
session. Tests never share a database with a running server, and no test
depends on the order of any other.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_TMP = Path(tempfile.mkdtemp(prefix="retailmind-tests-"))

# Must be set BEFORE app.config is imported anywhere.
os.environ["RETAILMIND_ENV_FILE"] = str(_TMP / "no.env")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP / 'test.db'}"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["EMBEDDING_PROVIDER"] = "hashed"
os.environ["CASSETTE_MODE"] = "off"

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from app.db import create_all, dispose, drop_all, session_scope  # noqa: E402
from app.evaluation import datasets as ds_mod  # noqa: E402
from app.rag.ingest import ingest_kb  # noqa: E402
from app.security import customer, guest, operator  # noqa: E402
from app.seed import seed  # noqa: E402
from app.tracing import new_trace, set_trace  # noqa: E402


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _database():
    await drop_all()
    await create_all()
    async with session_scope() as s:
        await seed(s)
    async with session_scope() as s:
        await ingest_kb(s)
    yield
    await dispose()


@pytest_asyncio.fixture
async def session():
    async with session_scope() as s:
        yield s


@pytest.fixture
def trace():
    t = new_trace()
    yield t
    set_trace(None)


@pytest.fixture
def naveen():
    return customer("CU-1001")


@pytest.fixture
def priya():
    return customer("CU-1002")


@pytest.fixture
def anon():
    return guest()


@pytest.fixture
def op():
    return operator("op-test")


@pytest.fixture(scope="session")
def datasets_dir():
    return REPO / "datasets"


@pytest.fixture(scope="session")
def attack_corpus():
    return ds_mod.load("adversarial/attack_corpus")


@pytest.fixture(scope="session")
def benign_corpus():
    return ds_mod.load("safety/benign_corpus")
