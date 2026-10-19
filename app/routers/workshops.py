"""Workshop endpoints — create, list, get, cancel."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Workshop
from app.schemas import WorkshopCancelResponse, WorkshopCreate, WorkshopResponse
from app.services.registration import RegistrationError, cancel_workshop

router = APIRouter(prefix="/workshops", tags=["workshops"])


@router.post("", status_code=201, response_model=WorkshopResponse)
async def create_workshop(
    body: WorkshopCreate,
    db: AsyncSession = Depends(get_db),
) -> Workshop:
    workshop = Workshop(
        title=body.title,
        description=body.description,
        starts_at=body.starts_at,
    )
    db.add(workshop)
    await db.commit()
    await db.refresh(workshop)
    return workshop


@router.get("", response_model=list[WorkshopResponse])
async def list_workshops(db: AsyncSession = Depends(get_db)) -> list[Workshop]:
    result = await db.execute(select(Workshop).order_by(Workshop.created_at.desc()))
    return list(result.scalars().all())


@router.get("/{workshop_id}", response_model=WorkshopResponse)
async def get_workshop(
    workshop_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Workshop:
    result = await db.execute(select(Workshop).where(Workshop.id == workshop_id))
    workshop = result.scalar_one_or_none()
    if workshop is None:
        raise HTTPException(status_code=404, detail=f"Workshop {workshop_id} not found")
    return workshop


@router.post("/{workshop_id}/cancel", response_model=WorkshopCancelResponse)
async def cancel_workshop_endpoint(
    workshop_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        workshop, jobs_queued = await cancel_workshop(db, workshop_id)
    except RegistrationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return {
        "id": workshop.id,
        "status": workshop.status.value,
        "cancellation_jobs_queued": jobs_queued,
    }
