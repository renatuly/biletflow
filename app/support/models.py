from uuid import UUID

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UUIDTimestampMixin


class SupportCase(UUIDTimestampMixin, Base):
    __tablename__ = "support_cases"
    requester_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    event_id: Mapped[UUID | None] = mapped_column(ForeignKey("events.id"), index=True)
    order_id: Mapped[UUID | None] = mapped_column(ForeignKey("orders.id"))
    ticket_id: Mapped[UUID | None] = mapped_column(ForeignKey("tickets.id"))
    assigned_to: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    category: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30), default="open")
    subject: Mapped[str] = mapped_column(String(200))


class SupportMessage(UUIDTimestampMixin, Base):
    __tablename__ = "support_messages"
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("support_cases.id", ondelete="CASCADE"), index=True
    )
    sender_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
