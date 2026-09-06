from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UUIDTimestampMixin


class EventStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class PaidSalesStatus(StrEnum):
    NOT_READY = "not_ready"
    READY = "ready"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class OrganizerProfile(UUIDTimestampMixin, Base):
    __tablename__ = "organizer_profiles"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    contact_name: Mapped[str] = mapped_column(String(200))
    organization_name: Mapped[str | None] = mapped_column(String(200))
    contact_phone: Mapped[str | None] = mapped_column(String(40))
    verification_status: Mapped[str] = mapped_column(String(30), default="not_started")
    payout_account_masked: Mapped[str | None] = mapped_column(String(100))
    payout_verified: Mapped[bool] = mapped_column(Boolean, default=False)


class Venue(UUIDTimestampMixin, Base):
    __tablename__ = "venues"

    name: Mapped[str] = mapped_column(String(250))
    address: Mapped[str] = mapped_column(String(500))
    city: Mapped[str] = mapped_column(String(120), default="Almaty")
    country: Mapped[str] = mapped_column(String(2), default="KZ")


class Event(UUIDTimestampMixin, Base):
    __tablename__ = "events"

    organizer_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    venue_id: Mapped[UUID] = mapped_column(ForeignKey("venues.id"))
    title: Mapped[str] = mapped_column(String(250), index=True)
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(100))
    images: Mapped[list] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), default=list)
    visibility: Mapped[str] = mapped_column(String(20), default="public")
    status: Mapped[str] = mapped_column(String(20), default=EventStatus.DRAFT)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    time_zone: Mapped[str] = mapped_column(String(80), default="Asia/Almaty")
    registration_opens_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    registration_closes_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    capacity: Mapped[int] = mapped_column(Integer)
    admission_type: Mapped[str] = mapped_column(String(30), default="general_admission")
    refund_policy: Mapped[str] = mapped_column(Text, default="No refunds")
    paid_sales_status: Mapped[str] = mapped_column(String(30), default=PaidSalesStatus.NOT_READY)
    activation_fee_paid: Mapped[bool] = mapped_column(Boolean, default=False)
    paid_terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StaffAssignment(UUIDTimestampMixin, Base):
    __tablename__ = "staff_assignments"

    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    permissions: Mapped[list] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), default=lambda: ["check_in"]
    )


class AuditLog(UUIDTimestampMixin, Base):
    __tablename__ = "audit_logs"

    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    event_id: Mapped[UUID | None] = mapped_column(ForeignKey("events.id"), index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), default=dict)
