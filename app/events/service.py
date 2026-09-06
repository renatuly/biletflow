from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.events.models import AuditLog, Event, StaffAssignment
from app.users.models import User, UserRole


def require_event_manager(db: Session, event_id: UUID, user: User) -> Event:
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(404, detail="Event not found")
    assigned = db.scalar(
        select(StaffAssignment).where(
            StaffAssignment.event_id == event_id, StaffAssignment.user_id == user.id
        )
    )
    if event.organizer_id != user.id and UserRole.PLATFORM_ADMIN not in user.roles and not assigned:
        raise HTTPException(403, detail="Not authorized for this event")
    return event


def audit(
    db: Session,
    user: User,
    event: Event | None,
    action: str,
    entity_type: str,
    entity_id: object,
    description: str,
) -> None:
    db.add(
        AuditLog(
            actor_id=user.id,
            event_id=event.id if event else None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            description=description,
        )
    )


def publish_event(db: Session, event: Event, user: User) -> Event:
    if event.status == "cancelled":
        raise HTTPException(409, detail="Cancelled events cannot be published")
    event.status = "published"
    audit(db, user, event, "event.published", "event", event.id, "Event published")
    db.commit()
    db.refresh(event)
    return event
