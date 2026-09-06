from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UUIDTimestampMixin


class Campaign(UUIDTimestampMixin, Base):
    __tablename__ = "campaigns"
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    opaque_token: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    discount_type: Mapped[str] = mapped_column(String(20))
    discount_value: Mapped[int] = mapped_column(Integer)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    max_redemptions: Mapped[int] = mapped_column(Integer)
    redemption_count: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class PromoRedemption(UUIDTimestampMixin, Base):
    __tablename__ = "promo_redemptions"
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id"), index=True)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), unique=True)
    discount_minor: Mapped[int] = mapped_column(Integer)
