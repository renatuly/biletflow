from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import get_settings
from app.events.models import StaffAssignment
from app.orders.models import Order
from app.platform.operations_models import EventInvitation, PlatformSettings


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def settings(db):
    row = db.scalar(select(PlatformSettings).where(PlatformSettings.key == "platform"))
    if row:
        return row
    config = get_settings()
    return PlatformSettings(
        activation_fee_minor=config.app_activation_fee_minor,
        processing_fee_bps=config.app_processing_fee_bps,
        hold_minutes=config.app_ticket_hold_minutes,
        version=1,
    )


def can_manage(db, event, user):
    if not user:
        return False
    return (
        event.organizer_id == user.id
        or "platform_admin" in user.roles
        or bool(
            db.scalar(
                select(StaffAssignment.id).where(
                    StaffAssignment.event_id == event.id, StaffAssignment.user_id == user.id
                )
            )
        )
    )


def require_access(db, event, user, *, purchasing=False):
    if not event:
        raise HTTPException(404, detail="Event not found")
    if event.visibility != "private" or can_manage(db, event, user):
        return event
    if user:
        grant = db.scalar(
            select(EventInvitation).where(
                EventInvitation.event_id == event.id,
                EventInvitation.accepted_by == user.id,
                EventInvitation.status == "accepted",
                EventInvitation.expires_at > datetime.now(UTC),
            )
        )
        if grant and utc(grant.expires_at) > datetime.now(UTC):
            return event
        if not purchasing and db.scalar(
            select(Order.id).where(
                Order.event_id == event.id,
                Order.user_id == user.id,
                Order.status.in_(["confirmed", "refunded", "cancelled"]),
            )
        ):
            return event
    raise HTTPException(403, detail="An active invitation for your account is required")
