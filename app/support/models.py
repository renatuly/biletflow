from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UUIDTimestampMixin


class SupportCase(UUIDTimestampMixin, Base):
    __tablename__ = "support_cases"
    __table_args__ = (
        Index("uq_support_case_submission", "requester_id", "client_key", unique=True),
    )
    requester_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    flow: Mapped[str] = mapped_column(
        String(30), default="attendee", server_default="attendee", index=True
    )
    client_key: Mapped[str | None] = mapped_column(String(128))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    event_id: Mapped[UUID | None] = mapped_column(ForeignKey("events.id"), index=True)
    order_id: Mapped[UUID | None] = mapped_column(ForeignKey("orders.id"))
    ticket_id: Mapped[UUID | None] = mapped_column(ForeignKey("tickets.id"))
    assigned_to: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    category: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30), default="open")
    subject: Mapped[str] = mapped_column(String(200))


class SupportMessage(UUIDTimestampMixin, Base):
    __tablename__ = "support_messages"
    __table_args__ = (
        Index("uq_support_message_submission", "case_id", "sender_id", "client_key", unique=True),
    )
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("support_cases.id", ondelete="CASCADE"), index=True
    )
    sender_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    client_key: Mapped[str | None] = mapped_column(String(128))
    body: Mapped[str] = mapped_column(Text)


class SupportChange(UUIDTimestampMixin, Base):
    __tablename__ = "support_changes"
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("support_cases.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(30))
    old_value: Mapped[str | None] = mapped_column(String(100))
    new_value: Mapped[str | None] = mapped_column(String(100))
