"""Notification status endpoint."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import NotificationDelivery
from app.schemas import NotificationResponse

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/{delivery_id}", response_model=NotificationResponse)
async def get_notification(
    delivery_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> NotificationDelivery:
    result = await db.execute(
        select(NotificationDelivery).where(NotificationDelivery.id == delivery_id)
    )
    delivery = result.scalar_one_or_none()
    if delivery is None:
        raise HTTPException(
            status_code=404, detail=f"Notification delivery {delivery_id} not found"
        )
    return delivery
