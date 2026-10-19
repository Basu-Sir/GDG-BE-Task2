"""Registration service — atomic registration + confirmation job creation.

Key invariants enforced here:
  1. Workshop row is locked (SELECT FOR UPDATE) before checking status.
  2. Registration row and confirmation notification job are inserted in the
     same transaction.
  3. The unique DB constraint on (workshop_id, member_id) is the final
     authority; a known IntegrityError is mapped to 409.
  4. The notifier is NEVER called from this path.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DeliveryStatus,
    EventType,
    NotificationDelivery,
    Registration,
    Workshop,
    WorkshopStatus,
)

logger = logging.getLogger(__name__)


class RegistrationError(Exception):
    """Base for business-rule violations."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


async def register_member(
    db: AsyncSession,
    workshop_id: uuid.UUID,
    member_id: str,
) -> tuple[Registration, NotificationDelivery]:
    """Register *member_id* for *workshop_id*.

    Returns the (registration, notification_delivery) tuple on success.
    Raises RegistrationError on any business-rule violation.
    """
    # ── 1. Lock workshop row ──────────────────────────────────────────
    stmt = (
        select(Workshop)
        .where(Workshop.id == workshop_id)
        .with_for_update()
    )
    result = await db.execute(stmt)
    workshop = result.scalar_one_or_none()

    if workshop is None:
        raise RegistrationError(404, f"Workshop {workshop_id} not found")

    if workshop.status == WorkshopStatus.CANCELLED:
        raise RegistrationError(409, f"Workshop {workshop_id} is cancelled")

    # ── 2. Insert registration ────────────────────────────────────────
    registration = Registration(
        id=uuid.uuid4(),
        workshop_id=workshop_id,
        member_id=member_id,
    )
    db.add(registration)

    # ── 3. Insert confirmation job (same txn) ─────────────────────────
    event_key = f"registration-confirmed:{registration.id}"
    now = datetime.now(timezone.utc)

    delivery = NotificationDelivery(
        id=uuid.uuid4(),
        event_key=event_key,
        event_type=EventType.REGISTRATION_CONFIRMED,
        workshop_id=workshop_id,
        registration_id=registration.id,
        member_id=member_id,
        payload={
            "event_type": EventType.REGISTRATION_CONFIRMED.value,
            "workshop_id": str(workshop_id),
            "workshop_title": workshop.title,
            "registration_id": str(registration.id),
            "member_id": member_id,
            "message": "Your registration is confirmed.",
        },
        status=DeliveryStatus.PENDING,
        next_attempt_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(delivery)

    # ── 4. Commit atomically ──────────────────────────────────────────
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        # Only map the *known* unique-constraint violation to 409.
        error_info = str(exc.orig) if exc.orig else str(exc)
        if "uq_workshop_member" in error_info or "unique" in error_info.lower():
            raise RegistrationError(
                409, f"Member {member_id} is already registered for workshop {workshop_id}"
            ) from exc
        # Unknown DB error — re-raise as-is so we don't mask bugs.
        raise

    logger.info(
        "Registered member=%s for workshop=%s  reg=%s  delivery=%s",
        member_id,
        workshop_id,
        registration.id,
        delivery.id,
    )
    return registration, delivery


async def cancel_workshop(
    db: AsyncSession,
    workshop_id: uuid.UUID,
) -> tuple[Workshop, int]:
    """Cancel a workshop and fan-out one cancellation job per registration.

    Returns (workshop, jobs_queued).
    Raises RegistrationError on violations.
    """
    # ── 1. Lock workshop row ──────────────────────────────────────────
    stmt = (
        select(Workshop)
        .where(Workshop.id == workshop_id)
        .with_for_update()
    )
    result = await db.execute(stmt)
    workshop = result.scalar_one_or_none()

    if workshop is None:
        raise RegistrationError(404, f"Workshop {workshop_id} not found")

    if workshop.status == WorkshopStatus.CANCELLED:
        raise RegistrationError(409, f"Workshop {workshop_id} is already cancelled")

    # ── 2. Mark cancelled ─────────────────────────────────────────────
    workshop.status = WorkshopStatus.CANCELLED

    # ── 3. Read registrations ─────────────────────────────────────────
    reg_stmt = select(Registration).where(Registration.workshop_id == workshop_id)
    reg_result = await db.execute(reg_stmt)
    registrations = reg_result.scalars().all()

    # ── 4. Insert one cancellation job per registration ───────────────
    now = datetime.now(timezone.utc)
    jobs_queued = 0

    for reg in registrations:
        event_key = f"workshop-cancelled:{workshop_id}:{reg.member_id}"
        delivery = NotificationDelivery(
            id=uuid.uuid4(),
            event_key=event_key,
            event_type=EventType.WORKSHOP_CANCELLED,
            workshop_id=workshop_id,
            registration_id=reg.id,
            member_id=reg.member_id,
            payload={
                "event_type": EventType.WORKSHOP_CANCELLED.value,
                "workshop_id": str(workshop_id),
                "workshop_title": workshop.title,
                "registration_id": str(reg.id),
                "member_id": reg.member_id,
                "message": f"Workshop '{workshop.title}' has been cancelled.",
            },
            status=DeliveryStatus.PENDING,
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(delivery)
        jobs_queued += 1

    # ── 5. Commit atomically ──────────────────────────────────────────
    await db.commit()

    logger.info(
        "Cancelled workshop=%s  jobs_queued=%d",
        workshop_id,
        jobs_queued,
    )
    return workshop, jobs_queued
