"""Registration endpoints — register member, list registrations."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import NotificationDelivery, Registration, Workshop
from app.schemas import RegistrationCreate, RegistrationResponse
from app.services.registration import RegistrationError, register_member

router = APIRouter(tags=["registrations"])


@router.post(
    "/workshops/{workshop_id}/registrations",
    status_code=201,
    response_model=RegistrationResponse,
)
async def create_registration(
    workshop_id: uuid.UUID,
    body: RegistrationCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        registration, delivery = await register_member(db, workshop_id, body.member_id)
    except RegistrationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return {
        "id": registration.id,
        "workshop_id": registration.workshop_id,
        "member_id": registration.member_id,
        "created_at": registration.created_at,
        "notification_status": delivery.status.value,
    }


@router.get(
    "/workshops/{workshop_id}/registrations",
    response_model=list[RegistrationResponse],
)
async def list_registrations(
    workshop_id: uuid.UUID,
    member_id: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    # Verify workshop exists
    ws_result = await db.execute(select(Workshop).where(Workshop.id == workshop_id))
    if ws_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=f"Workshop {workshop_id} not found")

    stmt = select(Registration).where(Registration.workshop_id == workshop_id)
    if member_id:
        stmt = stmt.where(Registration.member_id == member_id)
    stmt = stmt.order_by(Registration.created_at)

    result = await db.execute(stmt)
    registrations = result.scalars().all()

    # Fetch notification status for each registration
    out: list[dict] = []
    for reg in registrations:
        event_key = f"registration-confirmed:{reg.id}"
        nd_result = await db.execute(
            select(NotificationDelivery).where(
                NotificationDelivery.event_key == event_key
            )
        )
        nd = nd_result.scalar_one_or_none()
        out.append(
            {
                "id": reg.id,
                "workshop_id": reg.workshop_id,
                "member_id": reg.member_id,
                "created_at": reg.created_at,
                "notification_status": nd.status.value if nd else "UNKNOWN",
            }
        )
    return out
