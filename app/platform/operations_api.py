"""Invitation access, moderation, settings and clearly simulated payouts."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import current_user
from app.events.models import Event
from app.events.schemas import EventRead
from app.events.service import audit
from app.orders.models import Order
from app.platform.management import owner
from app.platform.operations_models import EventInvitation
from app.platform.operations_service import utc
from app.users.models import Notification, User

router = APIRouter(tags=["invitations", "operations"])


class InviteWrite(BaseModel):
    email: EmailStr
    days: int = Field(default=14, ge=1, le=365)


class AcceptInvite(BaseModel):
    token: str = Field(min_length=16, max_length=300)


class Reason(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class Decision(BaseModel):
    status: str = Field(pattern="^(open|under_review|resolved|dismissed)$")
    resolution: str = Field(min_length=3, max_length=2000)


class SettingsWrite(BaseModel):
    activation_fee_minor: int = Field(ge=0, le=100000000)
    processing_fee_bps: int = Field(ge=0, le=3000)
    hold_minutes: int = Field(ge=1, le=60)
    version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)


class PayoutWrite(BaseModel):
    status: str = Field(pattern="^(pending|processing|paid|blocked)$")
    note: str = Field(min_length=3, max_length=1000)


def notify(db, user_id, event, kind, message):
    db.add(
        Notification(
            user_id=user_id,
            kind=kind,
            title=event.title,
            message=message,
            data={"event_id": str(event.id)},
        )
    )


def require_text(text):
    if len(text.strip()) < 3:
        raise HTTPException(422, detail="Please provide a meaningful reason")


def invitation_json(i):
    return dict(
        id=str(i.id),
        email=i.email,
        expires_at=i.expires_at,
        status="expired"
        if i.status != "revoked" and utc(i.expires_at) <= datetime.now(UTC)
        else i.status,
        created_at=i.created_at,
    )


@router.get("/events/{event_id}/invitations")
def invitations(event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    owner(db, event_id, user)
    return [
        invitation_json(i)
        for i in db.scalars(
            select(EventInvitation)
            .where(EventInvitation.event_id == event_id)
            .order_by(EventInvitation.created_at.desc())
        )
    ]


@router.post("/events/{event_id}/invitations", status_code=201)
def invite(
    event_id: UUID,
    data: InviteWrite,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    event = owner(db, event_id, user)
    if event.visibility != "private" or event.status not in ("draft", "published"):
        raise HTTPException(409, detail="Invitations are available for active private events only")
    db.scalar(select(Event).where(Event.id == event.id).with_for_update())
    if db.scalar(
        select(EventInvitation.id).where(
            EventInvitation.event_id == event.id,
            EventInvitation.email == str(data.email).lower(),
            EventInvitation.status.in_(["pending", "accepted"]),
            EventInvitation.expires_at > datetime.now(UTC),
        )
    ):
        raise HTTPException(
            409, detail="Revoke the existing invitation before creating another for this email"
        )
    token = secrets.token_urlsafe(32)
    row = EventInvitation(
        event_id=event.id,
        email=str(data.email).lower(),
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(days=data.days),
    )
    db.add(row)
    db.flush()
    audit(
        db,
        user,
        event,
        "invitation.created",
        "invitation",
        row.id,
        "Created an email-bound private-event invitation",
    )
    db.commit()
    return {**invitation_json(row), "token": token}


@router.post("/events/{event_id}/invitations/{invitation_id}/revoke")
def revoke(
    event_id: UUID,
    invitation_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    event = owner(db, event_id, user)
    db.scalar(select(Event).where(Event.id == event.id).with_for_update())
    row = db.get(EventInvitation, invitation_id)
    if not row or row.event_id != event.id:
        raise HTTPException(404, detail="Invitation not found")
    row.status = "revoked"
    if row.accepted_by:
        from app.orders.reservations import release

        orders = db.scalars(
            select(Order)
            .where(
                Order.event_id == event.id,
                Order.user_id == row.accepted_by,
                Order.status == "pending",
            )
            .order_by(Order.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        for order in orders:
            release(db, order, "cancelled", user)
    audit(
        db,
        user,
        event,
        "invitation.revoked",
        "invitation",
        row.id,
        "Revoked private-event access for future purchases",
    )
    db.commit()
    return invitation_json(row)


@router.post("/invitations/accept", response_model=EventRead)
def accept(data: AcceptInvite, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.scalar(
        select(EventInvitation).where(
            EventInvitation.token_hash == hashlib.sha256(data.token.strip().encode()).hexdigest()
        )
    )
    if not row:
        raise HTTPException(404, detail="Invitation is invalid or no longer available")
    event = db.scalar(select(Event).where(Event.id == row.event_id).with_for_update())
    row = db.scalar(
        select(EventInvitation)
        .where(EventInvitation.id == row.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row.status == "revoked" or utc(row.expires_at) <= datetime.now(UTC):
        raise HTTPException(409, detail="This invitation expired or was revoked")
    if row.email.lower() != user.email.lower():
        raise HTTPException(403, detail="Sign in using the email address invited by the organizer")
    if event.status != "published" or event.visibility != "private":
        raise HTTPException(409, detail="This private event is not currently available")
    if row.accepted_by and row.accepted_by != user.id:
        raise HTTPException(403, detail="Invitation belongs to another account")
    if row.status != "accepted":
        row.accepted_by = user.id
        row.status = "accepted"
        audit(
            db,
            user,
            event,
            "invitation.accepted",
            "invitation",
            row.id,
            "Accepted private-event invitation",
        )
    db.commit()
    return event


@router.get("/events/invited/mine", response_model=list[EventRead])
def invited_events(user: User = Depends(current_user), db: Session = Depends(get_db)):
    ids = select(EventInvitation.event_id).where(
        EventInvitation.accepted_by == user.id,
        EventInvitation.status == "accepted",
        EventInvitation.expires_at > datetime.now(UTC),
    )
    return list(
        db.scalars(
            select(Event)
            .where(Event.id.in_(ids), Event.visibility == "private", Event.status == "published")
            .order_by(Event.starts_at)
        )
    )
