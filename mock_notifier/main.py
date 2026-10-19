"""Mock Notification Service.

A standalone FastAPI service that simulates notification delivery with
configurable behaviour: success, error, or timeout.

Maintains a persistent idempotency ledger in PostgreSQL so that:
  - Duplicate Idempotency-Key requests return success without creating
    a second logical delivery.
  - Deliveries survive service restarts.

Endpoints:
  POST /notify           — accept a notification (subject to mode)
  GET  /deliveries       — list all logical deliveries
  GET  /deliveries/{key} — look up by event_key
  GET  /health           — liveness check
  POST /admin/mode       — switch mode (demo-only, non-production)
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, String, Text, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://workshop_app:local_dev_password@db:5432/workshops",
)
MOCK_NOTIFY_MODE = os.getenv("MOCK_NOTIFY_MODE", "success")
MOCK_NOTIFY_TIMEOUT_SECONDS = float(os.getenv("MOCK_NOTIFY_TIMEOUT_SECONDS", "10"))

# ── Database ──────────────────────────────────────────────────────────

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class MockDelivery(Base):
    """Persistent idempotency ledger for the mock notifier."""

    __tablename__ = "mock_deliveries"

    idempotency_key = Column(String(512), primary_key=True)
    event_type = Column(String(64), nullable=False)
    recipient_id = Column(String(255), nullable=False)
    workshop_id = Column(String(255), nullable=False)
    payload = Column(JSONB, nullable=False, default=dict)
    delivered_at = Column(DateTime(timezone=True), nullable=False)


# ── Schemas ───────────────────────────────────────────────────────────


class NotifyRequest(BaseModel):
    event_key: str
    event_type: str
    recipient_id: str
    workshop_id: str
    message: str = ""


class ModeRequest(BaseModel):
    mode: str  # success | fail | timeout


class DeliveryOut(BaseModel):
    idempotency_key: str
    event_type: str
    recipient_id: str
    workshop_id: str
    payload: dict
    delivered_at: datetime


# ── App ───────────────────────────────────────────────────────────────

# Global mutable mode — simple enough for a demo service.
_current_mode: str = MOCK_NOTIFY_MODE


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create mock_deliveries table if it doesn't exist.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Mock notifier started  mode=%s", _current_mode)
    yield


app = FastAPI(
    title="Mock Notification Service",
    description="Simulates notification delivery for demos. NOT for production.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok", "mode": _current_mode}


@app.post("/admin/mode", tags=["admin"])
async def set_mode(body: ModeRequest) -> dict:
    """⚠ Demo-only: switch notification mode.  NOT a production endpoint."""
    global _current_mode
    valid = {"success", "fail", "timeout"}
    if body.mode not in valid:
        raise HTTPException(422, f"mode must be one of {valid}")
    old = _current_mode
    _current_mode = body.mode
    logger.info("Mode changed  %s → %s", old, _current_mode)
    return {"old_mode": old, "new_mode": _current_mode}


@app.post("/notify", tags=["notify"])
async def notify(body: NotifyRequest, request: Request) -> dict:
    """Accept a notification.  Behaviour depends on current mode."""
    global _current_mode

    # Prefer header, fall back to body field.
    idempotency_key = request.headers.get("Idempotency-Key", body.event_key)

    async with SessionLocal() as db:
        # ── Check idempotency ledger first ────────────────────────────
        existing = await db.execute(
            select(MockDelivery).where(
                MockDelivery.idempotency_key == idempotency_key
            )
        )
        if existing.scalar_one_or_none() is not None:
            logger.info("Duplicate key=%s — returning success", idempotency_key)
            return {"status": "duplicate_accepted", "idempotency_key": idempotency_key}

        # ── Apply mode ────────────────────────────────────────────────
        mode = _current_mode

        if mode == "fail":
            logger.info("FAIL mode  key=%s", idempotency_key)
            raise HTTPException(500, "Simulated failure")

        if mode == "timeout":
            # Record delivery FIRST (simulates: notifier accepted but
            # response is slow).  This tests the idempotency path.
            delivery = MockDelivery(
                idempotency_key=idempotency_key,
                event_type=body.event_type,
                recipient_id=body.recipient_id,
                workshop_id=body.workshop_id,
                payload={
                    "event_key": body.event_key,
                    "message": body.message,
                },
                delivered_at=datetime.now(timezone.utc),
            )
            db.add(delivery)
            await db.commit()
            logger.info(
                "TIMEOUT mode  key=%s — delivery recorded, now delaying %.0fs",
                idempotency_key,
                MOCK_NOTIFY_TIMEOUT_SECONDS,
            )
            await asyncio.sleep(MOCK_NOTIFY_TIMEOUT_SECONDS)
            return {"status": "delivered", "idempotency_key": idempotency_key}

        # ── success mode ──────────────────────────────────────────────
        delivery = MockDelivery(
            idempotency_key=idempotency_key,
            event_type=body.event_type,
            recipient_id=body.recipient_id,
            workshop_id=body.workshop_id,
            payload={
                "event_key": body.event_key,
                "message": body.message,
            },
            delivered_at=datetime.now(timezone.utc),
        )
        db.add(delivery)
        await db.commit()
        logger.info("DELIVERED  key=%s", idempotency_key)
        return {"status": "delivered", "idempotency_key": idempotency_key}


@app.get("/deliveries", tags=["deliveries"], response_model=list[DeliveryOut])
async def list_deliveries() -> list[MockDelivery]:
    async with SessionLocal() as db:
        result = await db.execute(
            select(MockDelivery).order_by(MockDelivery.delivered_at.desc())
        )
        return list(result.scalars().all())


@app.get("/deliveries/{event_key:path}", tags=["deliveries"], response_model=DeliveryOut)
async def get_delivery(event_key: str) -> MockDelivery:
    async with SessionLocal() as db:
        result = await db.execute(
            select(MockDelivery).where(MockDelivery.idempotency_key == event_key)
        )
        delivery = result.scalar_one_or_none()
        if delivery is None:
            raise HTTPException(404, f"No delivery for key={event_key}")
        return delivery
