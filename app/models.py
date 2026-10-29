"""SQLAlchemy ORM models.

Tables:
  - workshops
  - registrations  (unique on workshop_id + member_id)
  - notification_deliveries  (transactional outbox / durable queue)
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> uuid.UUID:
    return uuid.uuid4()


# ── Enums ─────────────────────────────────────────────────────────────


class WorkshopStatus(str, enum.Enum):
    OPEN = "OPEN"
    CANCELLED = "CANCELLED"


class EventType(str, enum.Enum):
    REGISTRATION_CONFIRMED = "REGISTRATION_CONFIRMED"
    WORKSHOP_CANCELLED = "WORKSHOP_CANCELLED"


class DeliveryStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DELIVERED = "DELIVERED"


# ── Base ──────────────────────────────────────────────────────────────


class Base(DeclarativeBase):
    pass


# ── Workshops ─────────────────────────────────────────────────────────


class Workshop(Base):
    __tablename__ = "workshops"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[WorkshopStatus] = mapped_column(
        Enum(WorkshopStatus, name="workshop_status"),
        nullable=False,
        default=WorkshopStatus.OPEN,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    registrations: Mapped[list["Registration"]] = relationship(
        back_populates="workshop", cascade="all, delete-orphan"
    )


# ── Registrations ─────────────────────────────────────────────────────


class Registration(Base):
    __tablename__ = "registrations"
    __table_args__ = (
        UniqueConstraint("workshop_id", "member_id", name="uq_workshop_member"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid
    )
    workshop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workshops.id"), nullable=False
    )
    member_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    workshop: Mapped["Workshop"] = relationship(back_populates="registrations")
    deliveries: Mapped[list["NotificationDelivery"]] = relationship(
        back_populates="registration", cascade="all, delete-orphan"
    )


# ── Notification Deliveries (Transactional Outbox) ────────────────────


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        Index("ix_nd_status_next_attempt", "status", "next_attempt_at"),
        Index("ix_nd_status_locked_until", "status", "locked_until"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid
    )
    event_key: Mapped[str] = mapped_column(
        String(512), unique=True, nullable=False
    )
    event_type: Mapped[EventType] = mapped_column(
        Enum(EventType, name="event_type"), nullable=False
    )
    workshop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workshops.id"), nullable=False
    )
    registration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("registrations.id"), nullable=True
    )
    registration: Mapped["Registration | None"] = relationship(
        back_populates="deliveries"
    )
    member_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus, name="delivery_status"),
        nullable=False,
        default=DeliveryStatus.PENDING,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=_utcnow
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
