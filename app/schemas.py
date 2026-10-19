"""Pydantic request / response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ── Workshop ──────────────────────────────────────────────────────────


class WorkshopCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    starts_at: datetime


class WorkshopResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None
    starts_at: datetime
    status: str
    created_at: datetime


class WorkshopCancelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    cancellation_jobs_queued: int


# ── Registration ──────────────────────────────────────────────────────


class RegistrationCreate(BaseModel):
    member_id: str = Field(..., min_length=1, max_length=255)


class RegistrationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workshop_id: uuid.UUID
    member_id: str
    created_at: datetime
    notification_status: str  # initial PENDING


# ── Notification ──────────────────────────────────────────────────────


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_key: str
    event_type: str
    workshop_id: uuid.UUID
    registration_id: uuid.UUID | None
    member_id: str
    status: str
    attempt_count: int
    next_attempt_at: datetime | None
    last_error: str | None
    delivered_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ── Generic error ─────────────────────────────────────────────────────


class ErrorResponse(BaseModel):
    detail: str
