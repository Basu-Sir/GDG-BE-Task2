"""Notification delivery worker.

Runs as a separate process.  Polls the notification_deliveries table for
due/reclaimable jobs, claims them with FOR UPDATE SKIP LOCKED, calls the
mock notifier over HTTP, and marks success or schedules retry.

Key design points:
  - DB transaction is SHORT: claim → commit → HTTP → short txn to record result.
  - Lease-based recovery: crashed PROCESSING rows become reclaimable after
    locked_until expires.
  - Capped exponential backoff with jitter.
  - Exceptions are caught per-job so one failure cannot kill the loop.
  - Idempotency key sent as Idempotency-Key header + event_key in body.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import signal
import sys
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_factory
from app.models import DeliveryStatus, NotificationDelivery

logger = logging.getLogger(__name__)

# ── Retry helpers ─────────────────────────────────────────────────────


def _next_backoff(attempt: int) -> float:
    """Capped exponential backoff with jitter."""
    delay = min(
        settings.retry_base_seconds * (2 ** (attempt - 1)),
        settings.retry_max_seconds,
    )
    # Add ±25 % jitter
    jitter = delay * 0.25 * (random.random() * 2 - 1)
    return max(0.1, delay + jitter)


# ── Claim jobs ────────────────────────────────────────────────────────


async def _claim_batch(db: AsyncSession) -> list[NotificationDelivery]:
    """Claim up to WORKER_BATCH_SIZE due/reclaimable jobs.

    Uses FOR UPDATE SKIP LOCKED so multiple workers can coexist.
    """
    now = datetime.now(timezone.utc)

    stmt = (
        select(NotificationDelivery)
        .where(
            or_(
                # Due PENDING jobs
                (NotificationDelivery.status == DeliveryStatus.PENDING)
                & (NotificationDelivery.next_attempt_at <= now),
                # Expired PROCESSING leases (crash recovery)
                (NotificationDelivery.status == DeliveryStatus.PROCESSING)
                & (NotificationDelivery.locked_until != None)  # noqa: E711
                & (NotificationDelivery.locked_until <= now),
            )
        )
        .order_by(NotificationDelivery.next_attempt_at.asc())
        .limit(settings.worker_batch_size)
        .with_for_update(skip_locked=True)
    )

    result = await db.execute(stmt)
    jobs = list(result.scalars().all())

    if not jobs:
        return []

    lease_expiry = now + timedelta(seconds=settings.worker_lease_seconds)

    for job in jobs:
        job.status = DeliveryStatus.PROCESSING
        job.locked_until = lease_expiry
        job.attempt_count += 1
        job.updated_at = now

    await db.commit()
    return jobs


# ── Deliver one job ───────────────────────────────────────────────────


async def _deliver(
    client: httpx.AsyncClient,
    job: NotificationDelivery,
) -> None:
    """Attempt HTTP delivery for a single job, then record outcome."""
    url = f"{settings.mock_notifier_url}/notify"

    body = {
        "event_key": job.event_key,
        "event_type": job.event_type.value,
        "recipient_id": job.member_id,
        "workshop_id": str(job.workshop_id),
        "message": job.payload.get("message", ""),
    }

    try:
        resp = await client.post(
            url,
            json=body,
            headers={"Idempotency-Key": job.event_key},
            timeout=settings.worker_http_timeout_seconds,
        )
    except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPError) as exc:
        await _mark_retry(job, f"{type(exc).__name__}: {exc}")
        return

    if resp.status_code in (200, 201):
        await _mark_delivered(job)
    elif resp.status_code in (429, 500, 502, 503, 504):
        await _mark_retry(job, f"HTTP {resp.status_code}: {resp.text[:200]}")
    else:
        # Unexpected 4xx — log and retry slowly
        logger.warning(
            "Unexpected %d from notifier for job=%s  body=%s",
            resp.status_code,
            job.id,
            resp.text[:300],
        )
        await _mark_retry(job, f"HTTP {resp.status_code}: {resp.text[:200]}")


async def _mark_delivered(job: NotificationDelivery) -> None:
    now = datetime.now(timezone.utc)
    async with async_session_factory() as db:
        await db.execute(
            update(NotificationDelivery)
            .where(NotificationDelivery.id == job.id)
            .values(
                status=DeliveryStatus.DELIVERED,
                delivered_at=now,
                locked_until=None,
                last_error=None,
                updated_at=now,
            )
        )
        await db.commit()
    logger.info(
        "DELIVERED  job=%s  event_key=%s  attempts=%d",
        job.id,
        job.event_key,
        job.attempt_count,
    )


async def _mark_retry(job: NotificationDelivery, error: str) -> None:
    now = datetime.now(timezone.utc)
    delay = _next_backoff(job.attempt_count)
    next_at = now + timedelta(seconds=delay)

    async with async_session_factory() as db:
        await db.execute(
            update(NotificationDelivery)
            .where(NotificationDelivery.id == job.id)
            .values(
                status=DeliveryStatus.PENDING,
                next_attempt_at=next_at,
                locked_until=None,
                last_error=error[:500],
                updated_at=now,
            )
        )
        await db.commit()
    logger.info(
        "RETRY  job=%s  event_key=%s  attempt=%d  delay=%.1fs  error=%s",
        job.id,
        job.event_key,
        job.attempt_count,
        delay,
        error[:120],
    )


# ── Main loop ─────────────────────────────────────────────────────────

_shutdown = False


def _handle_signal(sig: int, _frame) -> None:  # type: ignore[type-arg]
    global _shutdown
    logger.info("Received signal %s — shutting down gracefully", sig)
    _shutdown = True


async def run_worker() -> None:
    """Entry-point for the worker process."""
    global _shutdown

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info(
        "Worker started  poll=%.1fs  batch=%d  timeout=%.1fs  lease=%ds",
        settings.worker_poll_seconds,
        settings.worker_batch_size,
        settings.worker_http_timeout_seconds,
        settings.worker_lease_seconds,
    )

    async with httpx.AsyncClient() as client:
        while not _shutdown:
            try:
                async with async_session_factory() as db:
                    jobs = await _claim_batch(db)

                if jobs:
                    logger.info("Claimed %d job(s)", len(jobs))
                    # Process jobs concurrently but isolate failures
                    tasks = [_deliver(client, job) for job in jobs]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for i, result in enumerate(results):
                        if isinstance(result, Exception):
                            logger.error(
                                "Unhandled error delivering job=%s: %s",
                                jobs[i].id,
                                result,
                            )
                else:
                    await asyncio.sleep(settings.worker_poll_seconds)

            except Exception:
                logger.exception("Worker loop error — will retry in %ds", 5)
                await asyncio.sleep(5)

    logger.info("Worker stopped")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
