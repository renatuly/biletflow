from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UUIDTimestampMixin


class PlatformSettings(UUIDTimestampMixin, Base):
    __tablename__ = "platform_settings"
    key: Mapped[str] = mapped_column(String(30), unique=True, default="platform")
    activation_fee_minor: Mapped[int] = mapped_column(Integer)
    processing_fee_bps: Mapped[int] = mapped_column(Integer)
    hold_minutes: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)


class EventInvitation(UUIDTimestampMixin, Base):
    __tablename__ = "event_invitations"
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    accepted_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))


class EventReport(UUIDTimestampMixin, Base):
    __tablename__ = "event_reports"
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), index=True)
    reporter_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="open")
    resolution: Mapped[str] = mapped_column(Text, default="")


class OrderDispute(UUIDTimestampMixin, Base):
    __tablename__ = "order_disputes"
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    requester_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="open")
    resolution: Mapped[str] = mapped_column(Text, default="")


class SimulatedPayout(UUIDTimestampMixin, Base):
    __tablename__ = "simulated_payouts"
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    note: Mapped[str] = mapped_column(Text, default="")
    amount_minor: Mapped[int] = mapped_column(Integer, default=0)


class CheckoutAttempt(UUIDTimestampMixin, Base):
    __tablename__ = "checkout_attempts"
    __table_args__ = (UniqueConstraint("user_id", "request_key", name="uq_checkout_user_key"),)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    request_key: Mapped[str] = mapped_column(String(128))
    fingerprint: Mapped[str] = mapped_column(String(64))
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"))


class ActivationFeeRecord(UUIDTimestampMixin, Base):
    __tablename__ = "activation_fee_records"
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), unique=True)
    amount_minor: Mapped[int] = mapped_column(Integer)
