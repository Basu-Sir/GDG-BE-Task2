"""Shared pytest fixtures for integration tests.

Tests run against a real PostgreSQL instance.  The DATABASE_URL env var
must point to a test database (compose provides this automatically).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ── Ensure we have a DB URL ──────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://workshop_app:local_dev_password@localhost:5432/workshops",
)

# If running inside docker container, default to service names; otherwise localhost
_in_docker = os.path.exists("/.dockerenv")
API_BASE = os.getenv("API_BASE", "http://api:8000" if _in_docker else "http://localhost:8000")
NOTIFIER_BASE = os.getenv("NOTIFIER_BASE", "http://mock-notifier:8001" if _in_docker else "http://localhost:8001")


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def db_engine():
    engine = create_async_engine(DATABASE_URL, echo=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db(db_engine):
    session_factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session


@pytest.fixture
def api_url():
    return API_BASE


@pytest.fixture
def notifier_url():
    return NOTIFIER_BASE


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(base_url=API_BASE, timeout=10) as c:
        yield c


@pytest_asyncio.fixture
async def notifier_client():
    async with httpx.AsyncClient(base_url=NOTIFIER_BASE, timeout=10) as c:
        yield c


def unique_id() -> str:
    return f"member-{uuid.uuid4().hex[:8]}"


def future_time() -> str:
    return "2027-01-15T10:00:00+05:30"
