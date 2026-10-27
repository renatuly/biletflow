from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.events.models import Event, StaffAssignment
from app.support.models import SupportCase, SupportChange
from app.users.models import Notification, User


def admin(user):
    return "platform_admin" in user.roles


def handles_event(db: Session, event: Event, user: User):
    if event.organizer_id == user.id:
        return True
    return any(
        "support" in a.permissions
        for a in db.scalars(
            select(StaffAssignment).where(
                StaffAssignment.event_id == event.id, StaffAssignment.user_id == user.id
            )
        )
    )


def can_manage(db: Session, case: SupportCase, user: User):
    if admin(user):
        return True
    if case.flow != "attendee" or not case.event_id:
        return False
    event = db.get(Event, case.event_id)
    return bool(event and handles_event(db, event, user))


def visible_case(db, case_id, user, lock=False):
    query = select(SupportCase).where(SupportCase.id == case_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    case = db.scalar(query)
    if not case:
        raise HTTPException(404, detail="Support case not found")
    if case.requester_id != user.id and not can_manage(db, case, user):
        raise HTTPException(403, detail="Not authorized for this support queue")
    return case


def candidates(db: Session, case: SupportCase):
    users = list(db.scalars(select(User).where(User.is_active.is_(True))))
    if case.flow == "organizer_platform" or not case.event_id:
        return [u for u in users if admin(u)]
    event = db.get(Event, case.event_id)
    return [u for u in users if event and handles_event(db, event, u)]


def history(db, case, actor, kind, old, new):
    db.add(
        SupportChange(
            case_id=case.id,
            actor_id=actor.id,
            kind=kind,
            old_value=str(old) if old is not None else None,
            new_value=str(new) if new is not None else None,
        )
    )


def notify(db, case, actor, kind, title, message, extra=()):
    recipients = {case.requester_id, *extra}
    staff = candidates(db, case)
    allowed = {u.id for u in staff}
    if case.assigned_to in allowed:
        recipients.add(case.assigned_to)
    elif staff:
        recipients.update(u.id for u in staff)
    for user_id in recipients - {actor.id, None}:
        if user_id != case.requester_id and user_id not in allowed:
            continue
        db.add(
            Notification(
                user_id=user_id,
                kind=kind,
                title=title,
                message=message,
                data={"case_id": str(case.id), "flow": case.flow},
            )
        )
