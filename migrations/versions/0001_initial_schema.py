"""Initial schema — workshops, registrations, notification_deliveries.

Revision ID: 0001
Revises: None
Create Date: 2026-10-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Enums ─────────────────────────────────────────────────────
    workshop_status = postgresql.ENUM(
        "OPEN", "CANCELLED", name="workshop_status", create_type=False
    )
    event_type = postgresql.ENUM(
        "REGISTRATION_CONFIRMED", "WORKSHOP_CANCELLED",
        name="event_type", create_type=False,
    )
    delivery_status = postgresql.ENUM(
        "PENDING", "PROCESSING", "DELIVERED",
        name="delivery_status", create_type=False,
    )

    workshop_status.create(op.get_bind(), checkfirst=True)
    event_type.create(op.get_bind(), checkfirst=True)
    delivery_status.create(op.get_bind(), checkfirst=True)

    # ── workshops ─────────────────────────────────────────────────
    op.create_table(
        "workshops",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", workshop_status, nullable=False, server_default="OPEN"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    # ── registrations ─────────────────────────────────────────────
    op.create_table(
        "registrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workshop_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workshops.id"), nullable=False),
        sa.Column("member_id", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("workshop_id", "member_id", name="uq_workshop_member"),
    )

    # ── notification_deliveries ───────────────────────────────────
    op.create_table(
        "notification_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_key", sa.String(512), unique=True, nullable=False),
        sa.Column("event_type", event_type, nullable=False),
        sa.Column("workshop_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workshops.id"), nullable=False),
        sa.Column("registration_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("registrations.id"), nullable=True),
        sa.Column("member_id", sa.String(255), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("status", delivery_status, nullable=False, server_default="PENDING"),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "ix_nd_status_next_attempt",
        "notification_deliveries",
        ["status", "next_attempt_at"],
    )
    op.create_index(
        "ix_nd_status_locked_until",
        "notification_deliveries",
        ["status", "locked_until"],
    )

    # ── mock_deliveries (mock notifier ledger) ────────────────────
    op.create_table(
        "mock_deliveries",
        sa.Column("idempotency_key", sa.String(512), primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("recipient_id", sa.String(255), nullable=False),
        sa.Column("workshop_id", sa.String(255), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("mock_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_table("registrations")
    op.drop_table("workshops")

    op.execute("DROP TYPE IF EXISTS delivery_status")
    op.execute("DROP TYPE IF EXISTS event_type")
    op.execute("DROP TYPE IF EXISTS workshop_status")
