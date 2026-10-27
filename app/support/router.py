import hashlib
import json
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import current_user
from app.events.models import Event, StaffAssignment
from app.orders.models import Order, OrderItem, Refund
from app.orders.refunds import complete_refund, require_refund_owner
from app.support.access import (
    admin,
    can_manage,
    candidates,
    history,
    notify,
    visible_case,
)
from app.support.models import SupportCase, SupportChange, SupportMessage
from app.tickets.models import Ticket
from app.users.models import User

router = APIRouter(prefix="/support", tags=["support"])
ATTENDEE = {
    "report_event",
    "ticket_delivery",
    "payment",
    "refund",
    "seating",
    "event_information",
    "check_in",
    "account",
    "technical",
}
PLATFORM = {"account", "activation", "payment", "technical"}
STATUSES = {"open", "in_progress", "waiting_for_customer", "resolved"}


class CaseCreate(BaseModel):
    flow: Literal["attendee", "organizer_platform"] = "attendee"
    category: str
    subject: str = Field(min_length=3, max_length=200)
    message: str = Field(min_length=1, max_length=10000)
    event_id: UUID | None = None
    order_id: UUID | None = None
    ticket_id: UUID | None = None


class MessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10000)


class Assignment(BaseModel):
    user_id: UUID | None = None


class StaffGrant(BaseModel):
    email: str = Field(min_length=3, max_length=320)


def key_value(key):
    if key is not None and (not key.strip() or len(key) > 128):
        raise HTTPException(422, detail="Invalid Idempotency-Key")
    return key


def person(user):
    return {"id": str(user.id), "name": user.full_name or user.email}


def case_data(db, case, user):
    event = db.get(Event, case.event_id) if case.event_id else None
    order = db.get(Order, case.order_id) if case.order_id else None
    ticket = db.get(Ticket, case.ticket_id) if case.ticket_id else None
    assignee = db.get(User, case.assigned_to) if case.assigned_to else None
    manager = can_manage(db, case, user)
    # Legacy linked records are exposed only if their relationship is still valid.
    if order and (
        order.event_id != case.event_id
        or (case.flow == "attendee" and order.user_id != case.requester_id)
    ):
        order = None
    if ticket:
        item = db.get(OrderItem, ticket.order_item_id)
        if not order or not item or item.order_id != order.id:
            ticket = None
    refund = (
        db.scalar(
            select(Refund).where(Refund.order_id == order.id).order_by(Refund.created_at.desc())
        )
        if order
        else None
    )
    refund_owner = bool(event and (event.organizer_id == user.id or admin(user)))
    return {
        "id": str(case.id),
        "number": "BF-" + case.id.hex[:12].upper(),
        "flow": case.flow,
        "routing": "organizer"
        if case.flow == "attendee" and event and case.category != "report_event"
        else "platform",
        "requester_id": str(case.requester_id),
        "assigned_to": str(case.assigned_to) if case.assigned_to else None,
        "assignee_name": (assignee.full_name or assignee.email) if assignee else None,
        "event_id": str(event.id) if event else None,
        "event_title": event.title if event else None,
        "order_id": str(order.id) if order else None,
        "ticket_id": str(ticket.id) if ticket else None,
        "category": case.category,
        "status": case.status,
        "subject": case.subject,
        "created_at": case.created_at,
        "updated_at": case.updated_at,
        "order_status": order.status if order else None,
        "order_total_minor": order.total_minor if order else None,
        "currency": order.currency if order else None,
        "refund_status": refund.status if refund else None,
        "refund_amount_minor": refund.amount_minor if refund else None,
        "refund_is_simulation": refund.is_simulation if refund else None,
        "ticket_status": ticket.status if ticket else None,
        "refund_policy": event.refund_policy if event else None,
        "can_manage": manager,
        "can_refund": bool(
            case.flow == "attendee"
            and refund_owner
            and case.category == "refund"
            and order
            and order.status == "confirmed"
            and order.total_minor > 0
        ),
    }


def message_data(db, message):
    sender = db.get(User, message.sender_id)
    return {
        "id": str(message.id),
        "sender_id": str(message.sender_id),
        "sender_name": sender.full_name or sender.email,
        "body": message.body,
        "created_at": message.created_at,
    }


def ensure_manager(db, case, user):
    if not can_manage(db, case, user):
        raise HTTPException(403, detail="Not authorized to manage this support queue")


@router.get("/capabilities")
def capabilities(user: User = Depends(current_user), db: Session = Depends(get_db)):
    assignments = list(
        db.scalars(select(StaffAssignment).where(StaffAssignment.user_id == user.id))
    )
    return {
        "can_handle_attendee": admin(user)
        or "organizer" in user.roles
        or any("support" in a.permissions for a in assignments),
        "can_request_platform": "organizer" in user.roles,
        "can_handle_platform": admin(user),
    }


@router.get("/events/{event_id}/staff")
def support_members(
    event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    event = db.get(Event, event_id)
    if not event or (event.organizer_id != user.id and not admin(user)):
        raise HTTPException(403, detail="Only the organizer can manage support staff")
    assignments = db.scalars(select(StaffAssignment).where(StaffAssignment.event_id == event_id))
    result = []
    for assignment in assignments:
        if "support" in assignment.permissions:
            member = db.get(User, assignment.user_id)
            if member and member.is_active:
                result.append(person(member))
    return result


@router.post("/events/{event_id}/staff", status_code=201)
def grant_support(
    event_id: UUID,
    data: StaffGrant,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    event = db.get(Event, event_id)
    if not event or (event.organizer_id != user.id and not admin(user)):
        raise HTTPException(403, detail="Only the organizer can authorize support staff")
    member = db.scalar(
        select(User).where(User.email == data.email.strip().lower(), User.is_active.is_(True))
    )
    if not member:
        raise HTTPException(404, detail="Registered active user not found")
    assignment = db.scalar(
        select(StaffAssignment).where(
            StaffAssignment.event_id == event_id, StaffAssignment.user_id == member.id
        )
    )
    if not assignment:
        assignment = StaffAssignment(event_id=event_id, user_id=member.id, permissions=["support"])
        db.add(assignment)
    else:
        assignment.permissions = list(set(assignment.permissions) | {"support"})
    if "event_admin" not in member.roles and "organizer" not in member.roles and not admin(member):
        member.roles = [*member.roles, "event_admin"]
    from app.events.service import audit

    audit(db, user, event, "support.access_granted", "user", member.id, "Authorized event support")
    db.commit()
    return person(member)


@router.delete("/events/{event_id}/staff/{member_id}")
def revoke_support(
    event_id: UUID,
    member_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    event = db.get(Event, event_id)
    if not event or (event.organizer_id != user.id and not admin(user)):
        raise HTTPException(403, detail="Only the organizer can revoke support access")
    for assignment in db.scalars(
        select(StaffAssignment).where(
            StaffAssignment.event_id == event_id, StaffAssignment.user_id == member_id
        )
    ):
        assignment.permissions = [p for p in assignment.permissions if p != "support"]
    for case in db.scalars(
        select(SupportCase).where(
            SupportCase.event_id == event_id,
            SupportCase.flow == "attendee",
            SupportCase.assigned_to == member_id,
        )
    ):
        case.assigned_to = None
        case.updated_at = datetime.now(UTC)
        history(db, case, user, "assignment", member_id, None)
        notify(db, case, user, "support_assignment", "Support assignment changed", case.subject)
    from app.events.service import audit

    audit(db, user, event, "support.access_revoked", "user", member_id, "Revoked event support")
    db.commit()
    return {"status": "revoked"}


@router.post("/cases", status_code=201)
def create_case(
    data: CaseCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None),
):
    key = key_value(idempotency_key)
    fingerprint = hashlib.sha256(
        json.dumps(data.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()
    existing = (
        db.scalar(
            select(SupportCase).where(
                SupportCase.requester_id == user.id, SupportCase.client_key == key
            )
        )
        if key
        else None
    )
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(409, detail="Idempotency key was already used for another case")
        return case_data(db, existing, user)
    platform = data.flow == "organizer_platform"
    if platform and not {"organizer", "event_admin"}.intersection(user.roles):
        raise HTTPException(403, detail="Platform support requires an organizer or staff account")
    if (
        not platform
        and "event_admin" in user.roles
        and not {"attendee", "organizer", "platform_admin"}.intersection(user.roles)
    ):
        # A support agent can handle cases but cannot impersonate an attendee through this role.
        raise HTTPException(403, detail="Use the support inbox to handle attendee cases")
    if data.category == "report_event" and (
        "attendee" not in user.roles or not data.event_id or data.order_id or data.ticket_id
    ):
        raise HTTPException(403, detail="Event reports require an attendee and event context")
    if data.category not in (PLATFORM if platform else ATTENDEE):
        raise HTTPException(422, detail="Category does not belong to this support flow")
    if len(data.subject.strip()) < 3 or not data.message.strip():
        raise HTTPException(422, detail="Subject and message are required")
    order_id, event_id = data.order_id, data.event_id
    if data.ticket_id:
        ticket = db.get(Ticket, data.ticket_id)
        if not ticket or (not platform and ticket.owner_id != user.id):
            raise HTTPException(403, detail="Ticket does not belong to you")
        item = db.get(OrderItem, ticket.order_item_id)
        if order_id and order_id != item.order_id:
            raise HTTPException(422, detail="Ticket and order do not match")
        order_id = item.order_id
    order = None
    if order_id:
        order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
        if not order or (not platform and order.user_id != user.id):
            raise HTTPException(403, detail="Order does not belong to you")
        if event_id and event_id != order.event_id:
            raise HTTPException(422, detail="Order and event do not match")
        event_id = order.event_id
    event = db.get(Event, event_id) if event_id else None
    if event_id and not event:
        raise HTTPException(404, detail="Event not found")
    if not platform and event and not order:
        from app.platform.operations_service import require_access

        require_access(db, event, user)
    if platform and event and event.organizer_id != user.id:
        raise HTTPException(
            403, detail="Platform case context must belong to your organizer account"
        )
    if not platform and event and not order and event.status != "published":
        raise HTTPException(403, detail="An owned order is required for an unpublished event")
    if data.category == "refund" and order is not None:
        if not order or order.status != "confirmed" or order.total_minor <= 0:
            raise HTTPException(409, detail="Refund requests require a confirmed paid order")
        duplicate = db.scalar(
            select(SupportCase).where(
                SupportCase.order_id == order.id,
                SupportCase.flow == "attendee",
                SupportCase.category == "refund",
                SupportCase.status != "resolved",
            )
        )
        if duplicate:
            return case_data(db, duplicate, user)
    case = SupportCase(
        flow=data.flow,
        requester_id=user.id,
        event_id=event_id,
        order_id=order_id,
        ticket_id=data.ticket_id,
        category=data.category,
        subject=data.subject.strip(),
        assigned_to=event.organizer_id
        if event and not platform and data.category != "report_event"
        else None,
        client_key=key,
        request_fingerprint=fingerprint,
        status="open",
    )
    db.add(case)
    try:
        db.flush()
        db.add(
            SupportMessage(
                case_id=case.id, sender_id=user.id, body=data.message.strip(), client_key=key
            )
        )
        history(db, case, user, "created", None, "open")
        if case.assigned_to:
            history(db, case, user, "assignment", None, case.assigned_to)
        notify(db, case, user, "support_request", "New support request", case.subject)
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = (
            db.scalar(
                select(SupportCase).where(
                    SupportCase.requester_id == user.id, SupportCase.client_key == key
                )
            )
            if key
            else None
        )
        if not existing or existing.request_fingerprint != fingerprint:
            raise HTTPException(409, detail="Conflicting support submission") from None
        case = existing
    return case_data(db, case, user)


@router.get("/cases")
def list_cases(
    flow: Literal["attendee", "organizer_platform"] | None = None,
    view: Literal["mine", "inbox", "all"] = "all",
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    query = select(SupportCase).order_by(SupportCase.updated_at.desc())
    if flow:
        query = query.where(SupportCase.flow == flow)
    # Authorization is independent of assignment, so assigning a case cannot widen scope.
    cases = [
        c
        for c in db.scalars(query)
        if (
            (view != "inbox" and c.requester_id == user.id)
            or (view != "mine" and can_manage(db, c, user))
        )
    ]
    if (
        not (flow == "organizer_platform" and view == "mine")
        and "event_admin" in user.roles
        and not {"attendee", "organizer", "platform_admin"}.intersection(user.roles)
    ):
        assignments = db.scalars(select(StaffAssignment).where(StaffAssignment.user_id == user.id))
        if not any("support" in a.permissions for a in assignments):
            raise HTTPException(403, detail="Scanning access does not grant support access")
    return [case_data(db, case, user) for case in cases]


@router.get("/cases/{case_id}")
def read_case(case_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return case_data(db, visible_case(db, case_id, user), user)


@router.get("/cases/{case_id}/assignees")
def assignees(case_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    case = visible_case(db, case_id, user)
    ensure_manager(db, case, user)
    return [person(u) for u in candidates(db, case)]


@router.patch("/cases/{case_id}/assignee")
def assign(
    case_id: UUID,
    data: Assignment,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    case = visible_case(db, case_id, user, lock=True)
    ensure_manager(db, case, user)
    if data.user_id and data.user_id not in {u.id for u in candidates(db, case)}:
        raise HTTPException(403, detail="Assignee is outside this support queue")
    if case.assigned_to != data.user_id:
        old = case.assigned_to
        case.assigned_to = data.user_id
        case.updated_at = datetime.now(UTC)
        history(db, case, user, "assignment", old, data.user_id)
        notify(
            db,
            case,
            user,
            "support_assignment",
            "Support assignment changed",
            case.subject,
            extra=[old, data.user_id],
        )
        db.commit()
    return case_data(db, case, user)


@router.get("/cases/{case_id}/messages")
def messages(case_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    visible_case(db, case_id, user)
    return [
        message_data(db, m)
        for m in db.scalars(
            select(SupportMessage)
            .where(SupportMessage.case_id == case_id)
            .order_by(SupportMessage.created_at, SupportMessage.id)
        )
    ]


@router.post("/cases/{case_id}/messages", status_code=201)
def add_message(
    case_id: UUID,
    data: MessageCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None),
):
    case = visible_case(db, case_id, user, lock=True)
    key = key_value(idempotency_key)
    existing = (
        db.scalar(
            select(SupportMessage).where(
                SupportMessage.case_id == case.id,
                SupportMessage.sender_id == user.id,
                SupportMessage.client_key == key,
            )
        )
        if key
        else None
    )
    if existing:
        if existing.body != data.body.strip():
            raise HTTPException(409, detail="Idempotency key was already used for another message")
        return message_data(db, existing)
    if not data.body.strip():
        raise HTTPException(422, detail="Message is required")
    # Resolved cases remain readable; reopen explicitly. A completed refund stays separate.
    if case.status == "resolved":
        raise HTTPException(
            409, detail="Case is resolved; create a new case or ask authorized staff to reopen it"
        )
    message = SupportMessage(
        case_id=case.id, sender_id=user.id, body=data.body.strip(), client_key=key
    )
    db.add(message)
    if user.id == case.requester_id and case.status == "waiting_for_customer":
        history(db, case, user, "status", case.status, "in_progress")
        case.status = "in_progress"
    case.updated_at = datetime.now(UTC)
    notify(db, case, user, "support_message", "New support reply", case.subject)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = (
            db.scalar(
                select(SupportMessage).where(
                    SupportMessage.case_id == case.id,
                    SupportMessage.sender_id == user.id,
                    SupportMessage.client_key == key,
                )
            )
            if key
            else None
        )
        if not existing or existing.body != data.body.strip():
            raise HTTPException(409, detail="Conflicting message submission") from None
        message = existing
    return message_data(db, message)


@router.patch("/cases/{case_id}/status")
def change_status(
    case_id: UUID, status: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    case = visible_case(db, case_id, user, lock=True)
    ensure_manager(db, case, user)
    if status not in STATUSES:
        raise HTTPException(422, detail="Invalid support status")
    order = db.get(Order, case.order_id) if case.order_id else None
    if case.category == "refund" and order and order.status == "refunded" and status != "resolved":
        raise HTTPException(409, detail="Completed refund cases remain resolved")
    if case.status != status:
        history(db, case, user, "status", case.status, status)
        case.status = status
        case.updated_at = datetime.now(UTC)
        notify(
            db, case, user, "support_status", "Support status changed", f"{case.subject}: {status}"
        )
        db.commit()
    return case_data(db, case, user)


@router.get("/cases/{case_id}/history")
def case_history(case_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    visible_case(db, case_id, user)
    result = []
    for change in db.scalars(
        select(SupportChange)
        .where(SupportChange.case_id == case_id)
        .order_by(SupportChange.created_at, SupportChange.id)
    ):
        actor = db.get(User, change.actor_id)
        old_member = (
            db.get(User, UUID(change.old_value))
            if change.kind == "assignment" and change.old_value
            else None
        )
        new_member = (
            db.get(User, UUID(change.new_value))
            if change.kind == "assignment" and change.new_value
            else None
        )
        result.append(
            {
                "old_name": (old_member.full_name or old_member.email) if old_member else None,
                "new_name": (new_member.full_name or new_member.email) if new_member else None,
                "id": str(change.id),
                "kind": change.kind,
                "old_value": change.old_value,
                "new_value": change.new_value,
                "actor_name": actor.full_name or actor.email,
                "created_at": change.created_at,
            }
        )
    return result


@router.post("/cases/{case_id}/refund")
def approve_refund(
    case_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    case = visible_case(db, case_id, user)
    if case.flow != "attendee" or case.category != "refund" or not case.order_id:
        raise HTTPException(409, detail="Not an attendee refund request")
    order = db.get(Order, case.order_id)
    if not order or order.event_id != case.event_id or order.user_id != case.requester_id:
        raise HTTPException(409, detail="Refund case context does not match the order")
    require_refund_owner(db, order, user)
    if order.status != "refunded":
        # Resolving a conversation never performs this separate operation.
        complete_refund(db, order.id, f"Support request: {case.subject}"[:500], user)
        db.commit()
        db.refresh(case)
    return case_data(db, case, user)
