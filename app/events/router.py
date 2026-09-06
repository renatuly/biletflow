from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.dependencies import current_user, require_role
from app.events.models import (
    AuditLog,
    Event,
    OrganizerProfile,
    PaidSalesStatus,
    StaffAssignment,
    Venue,
)
from app.events.schemas import (
    EventCreate,
    EventRead,
    EventUpdate,
    OrganizerProfileRead,
    OrganizerProfileWrite,
)
from app.events.service import audit, publish_event, require_event_manager
from app.tickets.models import TicketType
from app.users.models import User, UserRole

router = APIRouter(tags=["events"])


@router.get("/events", response_model=list[EventRead])
def browse_events(q: str | None = None, db: Session = Depends(get_db)):
    statement = select(Event).where(Event.status == "published", Event.visibility == "public")
    if q:
        statement = statement.where(
            or_(Event.title.ilike(f"%{q}%"), Event.category.ilike(f"%{q}%"))
        )
    return list(db.scalars(statement.order_by(Event.starts_at)).all())


@router.get("/events/{event_id}", response_model=EventRead)
def read_event(event_id: UUID, db: Session = Depends(get_db)):
    event = db.get(Event, event_id)
    if not event or event.visibility == "private" or event.status == "draft":
        raise HTTPException(404, detail="Event not found")
    return event


@router.post("/events", response_model=EventRead, status_code=201)
async def create_event(
    data: EventCreate,
    user: User = Depends(require_role(UserRole.ORGANIZER)),
    db: Session = Depends(get_db),
):
    venue = Venue(**data.venue.model_dump())
    db.add(venue)
    db.flush()
    event = Event(organizer_id=user.id, venue_id=venue.id, **data.model_dump(exclude={"venue"}))
    db.add(event)
    db.flush()
    audit(db, user, event, "event.created", "event", event.id, "Draft event created")
    db.commit()
    db.refresh(event)
    return event


@router.patch("/events/{event_id}", response_model=EventRead)
def update_event(
    event_id: UUID,
    data: EventUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    event = require_event_manager(db, event_id, user)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(event, field, value)
    audit(db, user, event, "event.updated", "event", event.id, "Event details updated")
    db.commit()
    db.refresh(event)
    return event


@router.post("/events/{event_id}/publish", response_model=EventRead)
async def publish(
    event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    event = require_event_manager(db, event_id, user)
    if not db.scalar(select(TicketType).where(TicketType.event_id == event.id)):
        raise HTTPException(409, detail="Create at least one ticket type before publishing")
    return publish_event(db, event, user)


@router.post("/events/{event_id}/unpublish", response_model=EventRead)
def unpublish(event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    event = require_event_manager(db, event_id, user)
    event.status = "draft"
    audit(db, user, event, "event.unpublished", "event", event.id, "Event unpublished")
    db.commit()
    db.refresh(event)
    return event


@router.post("/events/{event_id}/cancel", response_model=EventRead)
def cancel(event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    event = require_event_manager(db, event_id, user)
    event.status = "cancelled"
    event.paid_sales_status = "suspended"
    audit(
        db, user, event, "event.cancelled", "event", event.id, "Event cancelled and sales stopped"
    )
    db.commit()
    db.refresh(event)
    return event


@router.post("/events/{event_id}/duplicate", response_model=EventRead, status_code=201)
def duplicate(event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    source = require_event_manager(db, event_id, user)
    copy = Event(
        organizer_id=user.id,
        venue_id=source.venue_id,
        title=f"Copy of {source.title}",
        description=source.description,
        category=source.category,
        images=source.images,
        visibility=source.visibility,
        starts_at=source.starts_at,
        ends_at=source.ends_at,
        time_zone=source.time_zone,
        registration_opens_at=source.registration_opens_at,
        registration_closes_at=source.registration_closes_at,
        capacity=source.capacity,
        admission_type=source.admission_type,
        refund_policy=source.refund_policy,
    )
    db.add(copy)
    db.flush()
    audit(db, user, copy, "event.duplicated", "event", copy.id, f"Duplicated from {source.id}")
    db.commit()
    db.refresh(copy)
    return copy


@router.get("/organizer/events", response_model=list[EventRead])
def organizer_events(
    user: User = Depends(require_role(UserRole.ORGANIZER)), db: Session = Depends(get_db)
):
    return list(
        db.scalars(
            select(Event).where(Event.organizer_id == user.id).order_by(Event.starts_at.desc())
        ).all()
    )


@router.put("/organizer-profile", response_model=OrganizerProfileRead)
def save_profile(
    data: OrganizerProfileWrite,
    user: User = Depends(require_role(UserRole.ORGANIZER)),
    db: Session = Depends(get_db),
):
    profile = db.scalar(select(OrganizerProfile).where(OrganizerProfile.user_id == user.id))
    if not profile:
        profile = OrganizerProfile(user_id=user.id, **data.model_dump())
        db.add(profile)
    else:
        for key, value in data.model_dump().items():
            setattr(profile, key, value)
    db.commit()
    db.refresh(profile)
    return profile


@router.post("/organizer-profile/verification/submit", response_model=OrganizerProfileRead)
def verify_profile(
    user: User = Depends(require_role(UserRole.ORGANIZER)), db: Session = Depends(get_db)
):
    profile = db.scalar(select(OrganizerProfile).where(OrganizerProfile.user_id == user.id))
    if not profile:
        raise HTTPException(409, detail="Create organizer profile first")
    profile.verification_status = "verified"
    db.commit()
    db.refresh(profile)
    return profile


@router.put("/organizer-profile/payout-account", response_model=OrganizerProfileRead)
def connect_payout(
    account_reference: str,
    user: User = Depends(require_role(UserRole.ORGANIZER)),
    db: Session = Depends(get_db),
):
    profile = db.scalar(select(OrganizerProfile).where(OrganizerProfile.user_id == user.id))
    if not profile:
        raise HTTPException(409, detail="Create organizer profile first")
    profile.payout_account_masked = f"****{account_reference[-4:]}"
    profile.payout_verified = True
    db.commit()
    db.refresh(profile)
    return profile


@router.get("/events/{event_id}/paid-sales")
def paid_sales_checklist(
    event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    event = require_event_manager(db, event_id, user)
    profile = db.scalar(
        select(OrganizerProfile).where(OrganizerProfile.user_id == event.organizer_id)
    )
    has_paid_ticket = bool(
        db.scalar(
            select(TicketType).where(TicketType.event_id == event.id, TicketType.kind == "paid")
        )
    )
    checklist = {
        "has_paid_ticket": has_paid_ticket,
        "identity_verified": bool(profile and profile.verification_status == "verified"),
        "payout_connected": bool(profile and profile.payout_verified),
        "activation_fee_paid": event.activation_fee_paid,
        "terms_accepted": event.paid_terms_accepted_at is not None,
    }
    return {
        "status": event.paid_sales_status,
        "checklist": checklist,
        "can_activate": all(checklist.values()),
        "is_simulation": True,
    }


@router.post("/events/{event_id}/paid-sales/accept-terms")
def accept_terms(event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    event = require_event_manager(db, event_id, user)
    event.paid_terms_accepted_at = datetime.now(UTC)
    audit(
        db, user, event, "paid_sales.terms_accepted", "event", event.id, "Paid-event terms accepted"
    )
    db.commit()
    return {"accepted": True, "terms_version": "2026-09"}


@router.post("/events/{event_id}/paid-sales/activation-fee")
def pay_activation_fee(
    event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    event = require_event_manager(db, event_id, user)
    event.activation_fee_paid = True
    audit(
        db, user, event, "paid_sales.fee_paid", "event", event.id, "Simulated activation fee paid"
    )
    db.commit()
    return {
        "status": "succeeded",
        "amount_minor": get_settings().app_activation_fee_minor,
        "currency": "KZT",
        "is_simulation": True,
    }


@router.post("/events/{event_id}/paid-sales/activate")
def activate_paid_sales(
    event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    event = require_event_manager(db, event_id, user)
    profile = db.scalar(
        select(OrganizerProfile).where(OrganizerProfile.user_id == event.organizer_id)
    )
    ready = (
        event.activation_fee_paid
        and event.paid_terms_accepted_at
        and profile
        and profile.verification_status == "verified"
        and profile.payout_verified
        and db.scalar(
            select(TicketType).where(TicketType.event_id == event.id, TicketType.kind == "paid")
        )
    )
    if not ready:
        raise HTTPException(409, detail="Paid-sales activation checklist is incomplete")
    event.paid_sales_status = PaidSalesStatus.ACTIVE
    audit(db, user, event, "paid_sales.activated", "event", event.id, "Paid sales activated")
    db.commit()
    return {"status": event.paid_sales_status, "is_simulation": True}


@router.post("/events/{event_id}/staff", status_code=201)
def assign_staff(
    event_id: UUID, email: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    event = require_event_manager(db, event_id, user)
    staff = db.scalar(select(User).where(User.email == email.lower()))
    if not staff:
        raise HTTPException(404, detail="User not found")
    if UserRole.EVENT_ADMIN not in staff.roles:
        staff.roles = [*staff.roles, UserRole.EVENT_ADMIN]
    assignment = StaffAssignment(
        event_id=event.id,
        user_id=staff.id,
        permissions=["check_in", "reverse_check_in", "manage_attendees"],
    )
    db.add(assignment)
    db.flush()
    audit(
        db,
        user,
        event,
        "staff.assigned",
        "staff_assignment",
        assignment.id,
        f"Assigned {staff.email}",
    )
    db.commit()
    return {
        "id": str(assignment.id),
        "user_id": str(staff.id),
        "permissions": assignment.permissions,
    }


@router.get("/events/{event_id}/history")
def event_history(
    event_id: UUID,
    action: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    require_event_manager(db, event_id, user)
    statement = select(AuditLog).where(AuditLog.event_id == event_id)
    if action:
        statement = statement.where(AuditLog.action == action)
    logs = db.scalars(statement.order_by(AuditLog.created_at.desc())).all()
    return [
        {
            "id": str(x.id),
            "timestamp": x.created_at,
            "actor_id": str(x.actor_id) if x.actor_id else None,
            "action": x.action,
            "entity_type": x.entity_type,
            "entity_id": x.entity_id,
            "description": x.description,
        }
        for x in logs
    ]
