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
from app.core.dependencies import current_user, require_role
from app.events.models import Event
from app.events.schemas import EventRead
from app.events.service import audit
from app.orders.models import Order, Refund
from app.platform.management import owner
from app.platform.operations_models import (
    EventInvitation,
    EventReport,
    OrderDispute,
    PlatformSettings,
    SimulatedPayout,
)
from app.platform.operations_service import require_access, settings, utc
from app.users.models import Notification, User, UserRole

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


@router.get("/admin/platform-settings")
def read_settings(
    admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN)), db: Session = Depends(get_db)
):
    row = settings(db)
    return dict(
        activation_fee_minor=row.activation_fee_minor,
        processing_fee_bps=row.processing_fee_bps,
        hold_minutes=row.hold_minutes,
        version=row.version,
    )


@router.patch("/admin/platform-settings")
def update_settings(
    data: SettingsWrite,
    admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN)),
    db: Session = Depends(get_db),
):
    require_text(data.reason)
    row = db.scalar(
        select(PlatformSettings).where(PlatformSettings.key == "platform").with_for_update()
    )
    if row is None:
        row = settings(db)
        db.add(row)
    if row.version != data.version:
        raise HTTPException(409, detail="Settings changed. Refresh before saving again")
    row.activation_fee_minor = data.activation_fee_minor
    row.processing_fee_bps = data.processing_fee_bps
    row.hold_minutes = data.hold_minutes
    row.version += 1
    audit(db, admin, None, "platform.settings_updated", "platform", "settings", data.reason.strip())
    from sqlalchemy.exc import IntegrityError

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, detail="Settings changed. Refresh before saving again") from None
    return read_settings(admin, db)


def report_json(db, row):
    event = db.get(Event, row.event_id)
    return dict(
        id=str(row.id),
        event_id=str(event.id),
        event_title=event.title,
        reason=row.reason,
        status=row.status,
        resolution=row.resolution,
        created_at=row.created_at,
    )


@router.post("/events/{event_id}/reports", status_code=201)
def report_event(
    event_id: UUID, data: Reason, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    require_text(data.reason)
    event = db.scalar(select(Event).where(Event.id == event_id).with_for_update())
    require_access(db, event, user)
    if event.status not in ("published", "suspended") or event.organizer_id == user.id:
        raise HTTPException(409, detail="This event cannot be reported by this account")
    if db.scalar(
        select(EventReport.id).where(
            EventReport.event_id == event.id,
            EventReport.reporter_id == user.id,
            EventReport.status.in_(["open", "under_review"]),
        )
    ):
        raise HTTPException(409, detail="You already have an active report for this event")
    row = EventReport(event_id=event.id, reporter_id=user.id, reason=data.reason.strip())
    db.add(row)
    db.flush()
    audit(
        db,
        user,
        event,
        "report.created",
        "report",
        row.id,
        "Event report submitted for administrator review",
    )
    for admin in db.scalars(select(User).where(User.is_active.is_(True))):
        if "platform_admin" in admin.roles:
            notify(db, admin.id, event, "event_report", "A new event report needs moderation.")
    db.commit()
    return report_json(db, row)


@router.get("/reports/mine")
def own_reports(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [
        report_json(db, r)
        for r in db.scalars(
            select(EventReport)
            .where(EventReport.reporter_id == user.id)
            .order_by(EventReport.created_at.desc())
        )
    ]


@router.get("/admin/event-reports")
def reports(
    admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN)), db: Session = Depends(get_db)
):
    return [
        report_json(db, r)
        for r in db.scalars(select(EventReport).order_by(EventReport.created_at.desc()))
    ]


@router.patch("/admin/event-reports/{report_id}")
def decide_report(
    report_id: UUID,
    data: Decision,
    admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN)),
    db: Session = Depends(get_db),
):
    require_text(data.resolution)
    row = db.scalar(select(EventReport).where(EventReport.id == report_id).with_for_update())
    if not row:
        raise HTTPException(404, detail="Report not found")
    if row.reporter_id == admin.id:
        raise HTTPException(403, detail="An administrator cannot review their own report")
    if row.status in ("resolved", "dismissed"):
        raise HTTPException(409, detail="This report is already closed")
    if data.status == "open":
        raise HTTPException(422, detail="Choose review, resolve or dismiss")
    row.status, row.resolution = data.status, data.resolution.strip()
    event = db.get(Event, row.event_id)
    notify(
        db,
        row.reporter_id,
        event,
        "report_status",
        "Report status: " + row.status + ". " + row.resolution,
    )
    audit(db, admin, event, "report." + row.status, "report", row.id, row.resolution)
    db.commit()
    return report_json(db, row)


def dispute_json(db, row):
    order = db.get(Order, row.order_id)
    event = db.get(Event, order.event_id)
    return dict(
        id=str(row.id),
        order_id=str(order.id),
        event_id=str(event.id),
        event_title=event.title,
        attendee_name=order.attendee_name,
        reason=row.reason,
        status=row.status,
        resolution=row.resolution,
        total_minor=order.total_minor,
        created_at=row.created_at,
        order_status=order.status,
    )


@router.post("/orders/{order_id}/disputes", status_code=201)
def dispute(
    order_id: UUID, data: Reason, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    require_text(data.reason)
    initial = db.get(Order, order_id)
    if not initial or initial.user_id != user.id:
        raise HTTPException(404, detail="Order not found")
    db.scalar(select(Event).where(Event.id == initial.event_id).with_for_update())
    order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if not order or order.user_id != user.id:
        raise HTTPException(404, detail="Order not found")
    if order.status != "confirmed" or order.total_minor <= 0:
        raise HTTPException(409, detail="Disputes require a confirmed paid order")
    if db.scalar(
        select(OrderDispute.id).where(
            OrderDispute.order_id == order.id, OrderDispute.status.in_(["open", "under_review"])
        )
    ):
        raise HTTPException(409, detail="An active dispute already exists for this order")
    row = OrderDispute(order_id=order.id, requester_id=user.id, reason=data.reason.strip())
    db.add(row)
    db.flush()
    event = db.get(Event, order.event_id)
    audit(db, user, event, "dispute.created", "dispute", row.id, "Simulated payment dispute opened")
    notify(
        db,
        event.organizer_id,
        event,
        "dispute_opened",
        "A payment dispute is under platform review. Payout is on hold.",
    )
    for admin in db.scalars(select(User).where(User.is_active.is_(True))):
        if "platform_admin" in admin.roles:
            notify(
                db, admin.id, event, "dispute_opened", "A simulated payment dispute needs review."
            )
    db.commit()
    return dispute_json(db, row)


@router.get("/disputes/mine")
def own_disputes(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [
        dispute_json(db, r)
        for r in db.scalars(
            select(OrderDispute)
            .where(OrderDispute.requester_id == user.id)
            .order_by(OrderDispute.created_at.desc())
        )
    ]


@router.get("/admin/disputes")
def disputes(
    admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN)), db: Session = Depends(get_db)
):
    return [
        dispute_json(db, r)
        for r in db.scalars(select(OrderDispute).order_by(OrderDispute.created_at.desc()))
    ]


@router.patch("/admin/disputes/{dispute_id}")
def decide_dispute(
    dispute_id: UUID,
    data: Decision,
    admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN)),
    db: Session = Depends(get_db),
):
    require_text(data.resolution)
    row = db.get(OrderDispute, dispute_id)
    if not row:
        raise HTTPException(404, detail="Dispute not found")
    initial = db.get(Order, row.order_id)
    db.scalar(select(Event).where(Event.id == initial.event_id).with_for_update())
    order = db.scalar(select(Order).where(Order.id == row.order_id).with_for_update())
    row = db.scalar(
        select(OrderDispute)
        .where(OrderDispute.id == dispute_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row.requester_id == admin.id:
        raise HTTPException(403, detail="An administrator cannot review their own dispute")
    if row.status in ("resolved", "dismissed") or data.status == "open":
        raise HTTPException(409, detail="Dispute is closed or transition is invalid")
    row.status, row.resolution = data.status, data.resolution.strip()
    event = db.get(Event, order.event_id)
    for uid in {row.requester_id, event.organizer_id}:
        notify(
            db,
            uid,
            event,
            "dispute_status",
            "Dispute status: " + row.status + ". " + row.resolution,
        )
    audit(db, admin, event, "dispute." + row.status, "dispute", row.id, row.resolution)
    db.commit()
    return dispute_json(db, row)


def financial_summary(db, event):
    from app.orders.reservations import expire_event

    expire_event(db, event.id)
    pending_hold = db.scalar(
        select(Order.id).where(Order.event_id == event.id, Order.status == "pending")
    )
    orders = list(
        db.scalars(
            select(Order).where(
                Order.event_id == event.id, Order.status.in_(["confirmed", "refunded"])
            )
        )
    )
    confirmed = [o for o in orders if o.status == "confirmed"]
    refunds = (
        list(
            db.scalars(
                select(Refund).where(
                    Refund.order_id.in_([o.id for o in orders]), Refund.status == "succeeded"
                )
            )
        )
        if orders
        else []
    )
    open_disputes = db.scalar(
        select(OrderDispute.id)
        .join(Order, Order.id == OrderDispute.order_id)
        .where(Order.event_id == event.id, OrderDispute.status.in_(["open", "under_review"]))
    )
    record = db.scalar(select(SimulatedPayout).where(SimulatedPayout.event_id == event.id))
    amount = max(0, sum(o.total_minor - o.processing_fee_minor for o in confirmed))
    eligible = (
        utc(event.ends_at) <= datetime.now(UTC)
        and event.status not in ("cancelled", "suspended", "draft")
        and not open_disputes
        and not pending_hold
        and amount > 0
    )
    status = record.status if record else "pending"
    if (
        open_disputes
        or event.status in ("cancelled", "suspended")
        or (record and record.status == "paid" and record.amount_minor != amount)
        or (not eligible and status in ("processing", "paid"))
    ):
        status = "blocked"
    return dict(
        event_id=str(event.id),
        event_title=event.title,
        gross_minor=sum(o.subtotal_minor for o in orders),
        collected_minor=sum(o.total_minor for o in confirmed),
        discounts_minor=sum(o.discount_minor for o in orders),
        fees_minor=sum(o.processing_fee_minor for o in confirmed),
        refunds_minor=sum(r.amount_minor for r in refunds),
        estimated_payout_minor=amount,
        status=status,
        eligible=bool(eligible),
        open_dispute=bool(open_disputes),
        pending_hold=bool(pending_hold),
        amount_minor=record.amount_minor if record else 0,
        recorded_paid=bool(record and record.status == "paid"),
        note=record.note if record else "",
        is_simulation=True,
        currency="KZT",
    )


@router.get("/events/{event_id}/financial-summary")
def financials(event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    event = owner(db, event_id, user)
    result = financial_summary(db, event)
    db.commit()
    return result


@router.get("/organizer/payouts")
def payouts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not {"organizer", "platform_admin"}.intersection(user.roles):
        raise HTTPException(403, detail="Organizer or administrator access required")
    query = select(Event).order_by(Event.starts_at.desc())
    if "platform_admin" not in user.roles:
        query = query.where(Event.organizer_id == user.id)
    result = [financial_summary(db, e) for e in db.scalars(query)]
    db.commit()
    return result


@router.patch("/admin/payouts/{event_id}")
def update_payout(
    event_id: UUID,
    data: PayoutWrite,
    admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN)),
    db: Session = Depends(get_db),
):
    require_text(data.note)
    event = db.scalar(select(Event).where(Event.id == event_id).with_for_update())
    if not event:
        raise HTTPException(404, detail="Event not found")
    # Serialize with refunds/disputes before evaluating current eligibility.
    list(
        db.scalars(
            select(Order)
            .where(Order.event_id == event.id)
            .order_by(Order.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    summary = financial_summary(db, event)
    if data.status in ("processing", "paid") and not summary["eligible"]:
        raise HTTPException(
            409,
            detail="Payout requires an ended event, positive balance and no dispute or suspension",
        )
    row = db.scalar(
        select(SimulatedPayout).where(SimulatedPayout.event_id == event.id).with_for_update()
    )
    if row and row.status == "paid":
        raise HTTPException(409, detail="A recorded simulated payout cannot be sent twice")
    if row is None:
        row = SimulatedPayout(event_id=event.id)
        db.add(row)
    row.status, row.note = data.status, data.note.strip()
    row.amount_minor = summary["estimated_payout_minor"] if data.status == "paid" else 0
    audit(db, admin, event, "payout." + data.status, "event", event.id, row.note)
    notify(
        db,
        event.organizer_id,
        event,
        "payout_status",
        "Simulated payout status: " + data.status + ". " + row.note,
    )
    db.commit()
    return financial_summary(db, event)
