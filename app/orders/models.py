from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UUIDTimestampMixin


class OrderStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    PAYMENT_FAILED = "payment_failed"
    EXPIRED = "expired"


class Order(UUIDTimestampMixin, Base):
    __tablename__ = "orders"

    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), index=True)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    attendee_name: Mapped[str] = mapped_column(String(200))
    attendee_email: Mapped[str] = mapped_column(String(320), index=True)
    status: Mapped[str] = mapped_column(String(30), default=OrderStatus.PENDING)
    currency: Mapped[str] = mapped_column(String(3), default="KZT")
    subtotal_minor: Mapped[int] = mapped_column(Integer, default=0)
    discount_minor: Mapped[int] = mapped_column(Integer, default=0)
    processing_fee_minor: Mapped[int] = mapped_column(Integer, default=0)
    total_minor: Mapped[int] = mapped_column(Integer, default=0)

    @property
    def can_cancel_registration(self) -> bool:
        from sqlalchemy import select
        from sqlalchemy.orm import object_session

        from app.tickets.models import Ticket, TicketType

        db = object_session(self)
        if not db or self.status != "confirmed" or self.total_minor != 0:
            return False
        kinds = list(
            db.scalars(
                select(TicketType.kind)
                .join(OrderItem, OrderItem.ticket_type_id == TicketType.id)
                .where(OrderItem.order_id == self.id)
            )
        )
        checked = db.scalar(
            select(Ticket.id)
            .join(OrderItem, Ticket.order_item_id == OrderItem.id)
            .where(OrderItem.order_id == self.id, Ticket.status == "checked_in")
        )
        return bool(kinds) and all(k == "free" for k in kinds) and checked is None

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    campaign_id: Mapped[UUID | None] = mapped_column(ForeignKey("campaigns.id"))


class OrderItem(UUIDTimestampMixin, Base):
    __tablename__ = "order_items"

    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    ticket_type_id: Mapped[UUID] = mapped_column(ForeignKey("ticket_types.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price_minor: Mapped[int] = mapped_column(Integer)
    discount_minor: Mapped[int] = mapped_column(Integer, default=0)


class Payment(UUIDTimestampMixin, Base):
    __tablename__ = "payments"

    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    provider_reference: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="KZT")
    is_simulation: Mapped[bool] = mapped_column(default=True)


class Refund(UUIDTimestampMixin, Base):
    __tablename__ = "refunds"

    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    payment_id: Mapped[UUID | None] = mapped_column(ForeignKey("payments.id"))
    initiated_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    amount_minor: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="succeeded")
    reason: Mapped[str] = mapped_column(String(500))
    is_simulation: Mapped[bool] = mapped_column(default=True)
