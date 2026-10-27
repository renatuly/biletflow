"""Manual review of paid events; payment alone never permits publication."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.database import Base, UUIDTimestampMixin, get_db
from app.core.dependencies import current_user, require_role
from app.events.models import Event, OrganizerProfile, Venue
from app.events.schemas import EventRead
from app.events.service import audit
from app.tickets.models import TicketType
from app.users.models import Notification, User, UserRole


class PaidEventApplication(UUIDTimestampMixin, Base):
    __tablename__ = "paid_event_applications"
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending_review")
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text, default="")


def fingerprint(db, event):
    def value(v):
        if isinstance(v, datetime):
            return (v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)).isoformat()
        return str(v) if isinstance(v, UUID) else v

    fields = (
        "title",
        "description",
        "category",
        "venue_id",
        "visibility",
        "images",
        "starts_at",
        "ends_at",
        "time_zone",
        "registration_opens_at",
        "registration_closes_at",
        "capacity",
        "admission_type",
        "refund_policy",
    )
    data = {f: value(getattr(event, f)) for f in fields}
    venue = db.get(Venue, event.venue_id)
    data["venue"] = [venue.name, venue.address, venue.city] if venue else None
    tickets = db.scalars(
        select(TicketType).where(TicketType.event_id == event.id).order_by(TicketType.id)
    )
    data["tickets"] = [
        {
            f: value(getattr(t, f))
            for f in (
                "id",
                "name",
                "description",
                "kind",
                "price_minor",
                "quantity",
                "sales_start",
                "sales_end",
                "max_per_order",
                "is_hidden",
            )
        }
        for t in tickets
    ]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def application(db, event):
    return db.scalar(select(PaidEventApplication).where(PaidEventApplication.event_id == event.id))


def require_approval(db, event):
    record = application(db, event)
    if (
        not record
        or record.status != "approved"
        or event.paid_sales_status != "active"
        or record.snapshot_hash != fingerprint(db, event)
    ):
        raise HTTPException(409, detail="Platform Admin approval is required for this paid event")


def invalidate_review(db, event, user):
    # All event/ticket mutations and decisions serialize on the event row.
    db.scalar(select(Event).where(Event.id == event.id).with_for_update())
    record = application(db, event)
    if record and record.status in ("pending_review", "approved"):
        if event.status != "draft":
            raise HTTPException(
                409, detail="Unpublish the paid event before changing reviewed details"
            )
        record.status = "changes_required"
        record.reason = "Event details changed. Submit the updated event for review."
        event.paid_sales_status = "not_ready"
        audit(db, user, event, "paid_review.invalidated", "event", event.id, record.reason)


def owner_event(db, event_id, user):
    event = db.scalar(
        select(Event)
        .where(Event.id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not event:
        raise HTTPException(404, detail="Event not found")
    if event.organizer_id != user.id:
        raise HTTPException(403, detail="Only the event organizer can submit a paid event")
    if event.status != "draft":
        raise HTTPException(409, detail="Only draft events can be submitted for review")
    return event


def ready(db, event):
    profile = db.scalar(
        select(OrganizerProfile).where(OrganizerProfile.user_id == event.organizer_id)
    )
    return bool(
        event.activation_fee_paid
        and event.paid_terms_accepted_at
        and profile
        and profile.verification_status == "verified"
        and profile.payout_verified
        and event.refund_policy.strip()
        and db.scalar(
            select(TicketType.id).where(
                TicketType.event_id == event.id,
                TicketType.kind == "paid",
                TicketType.is_hidden.is_(False),
            )
        )
    )


def submit(db, event_id, user):
    event = owner_event(db, event_id, user)
    if not ready(db, event):
        raise HTTPException(
            409, detail="Complete organizer details, payout, fee and terms before review"
        )
    record = application(db, event)
    current = fingerprint(db, event)
    if (
        record
        and record.status in ("pending_review", "approved")
        and record.snapshot_hash == current
    ):
        return {"status": event.paid_sales_status, "is_simulation": True}
    if record is None:
        record = PaidEventApplication(event_id=event.id)
        db.add(record)
    record.status = "pending_review"
    record.snapshot_hash = current
    record.submitted_at = datetime.now(UTC)
    record.reviewed_at = None
    record.reviewed_by = None
    record.reason = ""
    event.paid_sales_status = "pending_review"
    for admin in db.scalars(select(User).where(User.is_active.is_(True))):
        if "platform_admin" in admin.roles:
            db.add(
                Notification(
                    user_id=admin.id,
                    kind="paid_event_submitted",
                    title="Paid event awaiting review",
                    message=event.title,
                    data={"event_id": str(event.id)},
                )
            )
    audit(
        db,
        user,
        event,
        "paid_review.submitted",
        "event",
        event.id,
        "Paid event submitted for manual review",
    )
    db.commit()
    return {"status": "pending_review", "is_simulation": True}


router = APIRouter(tags=["paid event applications"])


@router.post("/events/{event_id}/paid-sales/apply")
def submit_application(
    event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    return submit(db, event_id, user)


@router.get("/admin/paid-event-applications")
def list_applications(
    user: User = Depends(require_role(UserRole.PLATFORM_ADMIN)), db: Session = Depends(get_db)
):
    records = db.scalars(
        select(PaidEventApplication).order_by(PaidEventApplication.submitted_at.desc())
    )
    result = []
    for record in records:
        event = db.get(Event, record.event_id)
        profile = db.scalar(
            select(OrganizerProfile).where(OrganizerProfile.user_id == event.organizer_id)
        )
        result.append(
            {
                "event": EventRead.model_validate(event).model_dump(),
                "status": record.status,
                "reason": record.reason,
                "submitted_at": record.submitted_at,
                "reviewed_at": record.reviewed_at,
                "reviewed_by": record.reviewed_by,
                "activation_fee_paid": event.activation_fee_paid,
                "terms_accepted": event.paid_terms_accepted_at is not None,
                "profile": {
                    "contact_name": profile.contact_name,
                    "contact_phone": profile.contact_phone,
                    "organization_name": profile.organization_name,
                    "payout_account_masked": profile.payout_account_masked,
                    "verification_status": profile.verification_status,
                    "payout_verified": profile.payout_verified,
                }
                if profile
                else None,
                "is_simulation": True,
            }
        )
    return result


class ReviewDecision(BaseModel):
    reason: str = Field(default="", max_length=2000)


@router.post("/admin/paid-event-applications/{event_id}/{decision}")
def decide(
    event_id: UUID,
    decision: str,
    data: ReviewDecision,
    user: User = Depends(require_role(UserRole.PLATFORM_ADMIN)),
    db: Session = Depends(get_db),
):
    if decision not in ("approve", "reject"):
        raise HTTPException(404, detail="Unknown review decision")
    event = db.scalar(
        select(Event)
        .where(Event.id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not event:
        raise HTTPException(404, detail="Event not found")
    if event.organizer_id == user.id:
        raise HTTPException(403, detail="An administrator cannot review their own event")
    record = application(db, event)
    if not record or record.status != "pending_review" or event.status != "draft":
        raise HTTPException(409, detail="This event is not awaiting review")
    if decision == "reject" and not data.reason.strip():
        raise HTTPException(422, detail="A rejection reason is required")
    if decision == "approve" and (
        not ready(db, event) or record.snapshot_hash != fingerprint(db, event)
    ):
        raise HTTPException(
            409, detail="Event details changed or the activation checklist is incomplete"
        )
    record.status = "approved" if decision == "approve" else "rejected"
    record.reason = data.reason.strip()
    record.reviewed_at = datetime.now(UTC)
    record.reviewed_by = user.id
    event.paid_sales_status = "active" if decision == "approve" else "rejected"
    db.add(
        Notification(
            user_id=event.organizer_id,
            kind="paid_event_review",
            title="Paid event approved" if decision == "approve" else "Paid event rejected",
            message="Your paid event is approved. You can now publish it."
            if decision == "approve"
            else record.reason,
            data={"event_id": str(event.id), "status": record.status},
        )
    )
    audit(
        db,
        user,
        event,
        f"paid_review.{record.status}",
        "event",
        event.id,
        record.reason or "Manual approval completed",
    )
    db.commit()
    return {"status": record.status, "paid_sales_status": event.paid_sales_status}
