from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UUIDTimestampMixin


class TicketStatus(StrEnum):
    VALID = "valid"
    CHECKED_IN = "checked_in"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class TicketType(UUIDTimestampMixin, Base):
    __tablename__ = "ticket_types"

    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(10))
    price_minor: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="KZT")
    quantity: Mapped[int] = mapped_column(Integer)
    reserved_count: Mapped[int] = mapped_column(Integer, default=0)
    sold_count: Mapped[int] = mapped_column(Integer, default=0)
    refunded_count: Mapped[int] = mapped_column(Integer, default=0)
    sales_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sales_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    max_per_order: Mapped[int] = mapped_column(Integer, default=10)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)


class Ticket(UUIDTimestampMixin, Base):
    __tablename__ = "tickets"

    order_item_id: Mapped[UUID] = mapped_column(ForeignKey("order_items.id"), index=True)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), index=True)
    ticket_type_id: Mapped[UUID] = mapped_column(ForeignKey("ticket_types.id"))
    owner_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    attendee_name: Mapped[str] = mapped_column(String(200))
    attendee_email: Mapped[str] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(20), default=TicketStatus.VALID)
    qr_credential: Mapped[str] = mapped_column(Text, unique=True)
    section: Mapped[str | None] = mapped_column(String(80))
    row: Mapped[str | None] = mapped_column(String(30))
    seat: Mapped[str | None] = mapped_column(String(30))


class CheckInRecord(UUIDTimestampMixin, Base):
    __tablename__ = "check_in_records"
    __table_args__ = (
        UniqueConstraint("ticket_id", "reversed_at", name="uq_active_ticket_checkin"),
    )

    ticket_id: Mapped[UUID] = mapped_column(ForeignKey("tickets.id"), index=True)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), index=True)
    staff_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    device_id: Mapped[str | None] = mapped_column(String(200))
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
