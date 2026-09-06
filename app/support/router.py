from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import current_user
from app.events.models import Event
from app.support.models import SupportCase, SupportMessage
from app.users.models import Notification, User

router = APIRouter(prefix="/support", tags=["support"])


class CaseCreate(BaseModel):
    category: str = Field(
        pattern="^(ticket_delivery|payment|refund|seating|event_information|check_in|account|technical)$"
    )
    subject: str = Field(min_length=3, max_length=200)
    message: str = Field(min_length=1)
    event_id: UUID | None = None
    order_id: UUID | None = None
    ticket_id: UUID | None = None


class MessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10000)


def visible_case(db: Session, case_id: UUID, user: User) -> SupportCase:
    case = db.get(SupportCase, case_id)
    if not case:
        raise HTTPException(404, detail="Support case not found")
    event = db.get(Event, case.event_id) if case.event_id else None
    if (
        case.requester_id != user.id
        and case.assigned_to != user.id
        and (not event or event.organizer_id != user.id)
        and "platform_admin" not in user.roles
    ):
        raise HTTPException(403, detail="Not authorized")
    return case


@router.post("/cases", status_code=201)
def create_case(
    data: CaseCreate, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    case = SupportCase(requester_id=user.id, **data.model_dump(exclude={"message"}))
    db.add(case)
    db.flush()
    db.add(SupportMessage(case_id=case.id, sender_id=user.id, body=data.message))
    db.commit()
    db.refresh(case)
    return case


@router.get("/cases")
def list_cases(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if "platform_admin" in user.roles:
        return list(db.scalars(select(SupportCase).order_by(SupportCase.updated_at.desc())).all())
    owned_events = select(Event.id).where(Event.organizer_id == user.id)
    return list(
        db.scalars(
            select(SupportCase)
            .where(
                or_(
                    SupportCase.requester_id == user.id,
                    SupportCase.assigned_to == user.id,
                    SupportCase.event_id.in_(owned_events),
                )
            )
            .order_by(SupportCase.updated_at.desc())
        ).all()
    )


@router.get("/cases/{case_id}/messages")
def messages(case_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    visible_case(db, case_id, user)
    return list(
        db.scalars(
            select(SupportMessage)
            .where(SupportMessage.case_id == case_id)
            .order_by(SupportMessage.created_at)
        ).all()
    )


@router.post("/cases/{case_id}/messages", status_code=201)
def add_message(
    case_id: UUID,
    data: MessageCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    case = visible_case(db, case_id, user)
    message = SupportMessage(case_id=case.id, sender_id=user.id, body=data.body)
    db.add(message)
    recipient = case.requester_id if case.requester_id != user.id else case.assigned_to
    if recipient:
        db.add(
            Notification(
                user_id=recipient,
                kind="support_message",
                title="New support reply",
                message="A support case has a new reply.",
            )
        )
    db.commit()
    db.refresh(message)
    return message


@router.patch("/cases/{case_id}/status")
def change_status(
    case_id: UUID, status: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    case = visible_case(db, case_id, user)
    if status not in {"open", "in_progress", "waiting_for_customer", "resolved"}:
        raise HTTPException(422, detail="Invalid support status")
    if case.requester_id == user.id and "platform_admin" not in user.roles:
        raise HTTPException(403, detail="Only support staff can change status")
    case.status = status
    db.add(
        Notification(
            user_id=case.requester_id,
            kind="support_status",
            title="Support status changed",
            message=f"Case status is now {status}.",
        )
    )
    db.commit()
    return case
