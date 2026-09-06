from enum import StrEnum
from uuid import UUID

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UUIDTimestampMixin


class UserRole(StrEnum):
    ATTENDEE = "attendee"
    ORGANIZER = "organizer"
    EVENT_ADMIN = "event_admin"
    PLATFORM_ADMIN = "platform_admin"


class User(UUIDTimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    full_name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))
    preferred_locale: Mapped[str] = mapped_column(String(5), default="kk")
    roles: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), default=lambda: [UserRole.ATTENDEE]
    )
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Notification(UUIDTimestampMixin, Base):
    __tablename__ = "notifications"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), default=dict)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
