import io
from datetime import UTC, datetime
from uuid import UUID

import qrcode
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import current_user, optional_user
from app.events.models import Event, StaffAssignment, Venue
from app.events.schemas import EventRead
from app.events.service import audit, require_event_manager
from app.orders.models import Order, OrderItem
from app.tickets.models import CheckInRecord, Ticket, TicketStatus, TicketType
from app.tickets.schemas import TicketRead, TicketTypeCreate, TicketTypeRead
from app.users.models import User

router = APIRouter(tags=["tickets"])


@router.get("/events/{event_id}/ticket-types", response_model=list[TicketTypeRead])
def list_ticket_types(
    event_id: UUID, user: User | None = Depends(optional_user), db: Session = Depends(get_db)
):
    event = db.get(Event, event_id)
    if not event or event.status != "published":
        raise HTTPException(404, detail="Event not found")
    from app.platform.operations_service import require_access

    require_access(db, event, user, purchasing=True)
    from app.orders.reservations import expire_event

    expire_event(db, event_id)
    db.commit()
    return list(
        db.scalars(
            select(TicketType).where(
                TicketType.event_id == event_id, TicketType.is_hidden.is_(False)
            )
        ).all()
    )


@router.post("/events/{event_id}/ticket-types", response_model=TicketTypeRead, status_code=201)
async def create_ticket_type(
    event_id: UUID,
    data: TicketTypeCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    event = require_event_manager(db, event_id, user)
    if event.organizer_id != user.id and "platform_admin" not in user.roles:
        raise HTTPException(403, detail="Only the organizer can create ticket types")
    if event.status in ("cancelled", "suspended", "completed"):
        raise HTTPException(409, detail="Cannot add tickets to closed or suspended event")
    if data.kind == "paid" and not event.refund_policy.strip():
        raise HTTPException(422, detail="Paid events require a refund policy description")
    from app.events.paid_applications import invalidate_review

    if data.kind == "paid" and event.status != "draft":
        raise HTTPException(409, detail="Paid ticket types can only be added to a draft event")
    invalidate_review(db, event, user)
    if (
        sum(
            t.quantity if not t.is_hidden else t.sold_count + t.reserved_count
            for t in event.ticket_types
        )
        + data.quantity
        > event.capacity
    ):
        raise HTTPException(422, detail="Ticket quantities exceed event capacity")
    item = TicketType(event_id=event.id, **data.model_dump())
    db.add(item)
    db.flush()
    audit(db, user, event, "ticket_type.created", "ticket_type", item.id, f"Created {item.name}")
    db.commit()
    db.refresh(item)
    return item


@router.post("/events/{event_id}/ticket-types/{ticket_type_id}/hide", response_model=TicketTypeRead)
def hide_ticket_type(
    event_id: UUID,
    ticket_type_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    return set_visibility(db, event_id, ticket_type_id, user, True)


def set_visibility(db, event_id, ticket_type_id, user, hidden):
    event = db.scalar(
        select(Event)
        .where(Event.id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not event or (event.organizer_id != user.id and "platform_admin" not in user.roles):
        raise HTTPException(
            403, detail="Only the organizer or administrator may manage ticket visibility"
        )
    if event.status not in ("draft", "published"):
        raise HTTPException(
            409, detail="Closed or suspended events cannot change ticket visibility"
        )
    item = db.scalar(
        select(TicketType)
        .where(TicketType.id == ticket_type_id, TicketType.event_id == event.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not item:
        raise HTTPException(404, detail="Ticket type not found")
    if (
        not hidden
        and sum(
            t.quantity if not t.is_hidden else t.sold_count + t.reserved_count
            for t in event.ticket_types
            if t.id != item.id
        )
        + item.quantity
        > event.capacity
    ):
        raise HTTPException(422, detail="Restore would exceed event capacity")
    if item.is_hidden != hidden:
        from app.events.paid_applications import application, fingerprint, invalidate_review

        record = application(db, event)
        approved = (
            record
            and record.status == "approved"
            and record.snapshot_hash == fingerprint(db, event)
        )
        if not approved:
            invalidate_review(db, event, user)
        item.is_hidden = hidden
        db.flush()
        if approved:
            record.snapshot_hash = fingerprint(db, event)
        audit(
            db,
            user,
            event,
            "ticket_type.hidden" if hidden else "ticket_type.restored",
            "ticket_type",
            item.id,
            "Ticket visibility changed; existing admissions retained",
        )
    db.commit()
    return item


@router.post(
    "/events/{event_id}/ticket-types/{ticket_type_id}/restore", response_model=TicketTypeRead
)
def restore_ticket_type(
    event_id: UUID,
    ticket_type_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    return set_visibility(db, event_id, ticket_type_id, user, False)


@router.get("/events/{event_id}/inventory")
def inventory(event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    event = require_event_manager(db, event_id, user)
    from app.orders.reservations import expire_event

    expire_event(db, event.id)
    db.commit()
    rows = list(
        db.scalars(
            select(TicketType)
            .where(TicketType.event_id == event.id)
            .order_by(TicketType.created_at)
        )
    )
    result = []
    global_available = max(0, event.capacity - sum(t.sold_count + t.reserved_count for t in rows))
    for t in rows:
        checked = (
            db.scalar(
                select(func.count(Ticket.id)).where(
                    Ticket.ticket_type_id == t.id, Ticket.status == "checked_in"
                )
            )
            or 0
        )

        result.append(
            {
                **TicketTypeRead.model_validate(t).model_dump(),
                "available": min(
                    global_available, max(0, t.quantity - t.reserved_count - t.sold_count)
                ),
                "checked_in": checked,
                "editable": event.status == "draft"
                and not (t.reserved_count or t.sold_count or t.refunded_count),
            }
        )
    return {
        "event_status": event.status,
        "registration_opens_at": event.registration_opens_at,
        "registration_closes_at": event.registration_closes_at,
        "refund_policy": event.refund_policy,
        "capacity": event.capacity,
        "ticket_types": result,
    }


def ticket_details(db: Session, ticket: Ticket):
    item = db.get(OrderItem, ticket.order_item_id)
    order = db.get(Order, item.order_id)
    return TicketRead.model_validate(ticket).model_copy(
        update={
            "order_id": order.id,
            "can_request_refund": order.status == "confirmed" and order.total_minor > 0,
        }
    )


@router.get("/tickets", response_model=list[TicketRead])
async def my_tickets(user: User = Depends(current_user), db: Session = Depends(get_db)):
    tickets = list(
        db.scalars(
            select(Ticket).where(Ticket.owner_id == user.id).order_by(Ticket.created_at.desc())
        ).all()
    )
    return [ticket_details(db, ticket) for ticket in tickets]


@router.get("/tickets/{ticket_id}", response_model=TicketRead)
def read_ticket(ticket_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, detail="Ticket not found")
    event = db.get(Event, ticket.event_id)
    if (
        ticket.owner_id != user.id
        and event.organizer_id != user.id
        and "platform_admin" not in user.roles
    ):
        raise HTTPException(403, detail="Not authorized")
    return ticket_details(db, ticket)


def authorized_ticket(db: Session, ticket_id: UUID, user: User) -> tuple[Ticket, Event]:
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, detail="Ticket not found")
    event = db.get(Event, ticket.event_id)
    if (
        ticket.owner_id != user.id
        and event.organizer_id != user.id
        and "platform_admin" not in user.roles
    ):
        raise HTTPException(403, detail="Not authorized")
    return ticket, event


@router.get("/tickets/{ticket_id}/event", response_model=EventRead)
def ticket_event(
    ticket_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    _, event = authorized_ticket(db, ticket_id, user)
    return event


@router.get("/tickets/{ticket_id}/qr")
def ticket_qr(ticket_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ticket, event = authorized_ticket(db, ticket_id, user)
    if event.status == "cancelled" or ticket.status not in (
        TicketStatus.VALID,
        TicketStatus.CHECKED_IN,
    ):
        raise HTTPException(409, detail="Ticket is not valid for admission")
    image = qrcode.make(ticket.qr_credential)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return Response(stream.getvalue(), media_type="image/png")


@router.get("/tickets/{ticket_id}/pdf")
async def ticket_pdf(
    ticket_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    ticket, event = authorized_ticket(db, ticket_id, user)
    if event.status == "cancelled" or ticket.status not in (
        TicketStatus.VALID,
        TicketStatus.CHECKED_IN,
    ):
        raise HTTPException(409, detail="Ticket is not valid for admission")
    ticket_type = db.get(TicketType, ticket.ticket_type_id)
    venue = db.get(Venue, event.venue_id)
    qr_stream = io.BytesIO()
    qrcode.make(ticket.qr_credential).save(qr_stream, format="PNG")
    qr_stream.seek(0)
    output = io.BytesIO()
    page = canvas.Canvas(output, pagesize=A4)
    width, height = A4
    page.setTitle(f"BiletFlow Ticket {ticket.id}")
    page.setFont("Helvetica-Bold", 22)
    page.drawString(50, height - 70, event.title)
    page.setFont("Helvetica", 12)
    lines = [
        f"Date: {event.starts_at.isoformat()}",
        f"Venue: {venue.name}, {venue.address}",
        f"Ticket: {ticket_type.name}",
        f"Attendee: {ticket.attendee_name}",
        f"Ticket ID: {ticket.id}",
    ]
    if ticket.seat:
        lines.append(f"Seat: {ticket.section or ''} {ticket.row or ''} {ticket.seat}")
    for index, line in enumerate(lines):
        page.drawString(50, height - 110 - index * 22, line)
    page.drawImage(ImageReader(qr_stream), 50, 220, width=240, height=240, preserveAspectRatio=True)
    page.setFont("Helvetica", 9)
    page.drawString(
        50,
        200,
        "This QR code is the admission credential. Printed and digital copies admit only once.",
    )
    page.showPage()
    page.save()
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="ticket-{ticket.id}.pdf"'},
    )


def assigned_event(db: Session, event_id: UUID, user: User, permission: str = "check_in") -> Event:
    event = db.get(Event, event_id)
    assignment = (
        db.scalar(
            select(StaffAssignment).where(
                StaffAssignment.event_id == event_id, StaffAssignment.user_id == user.id
            )
        )
        if event
        else None
    )
    if not event or (
        (not assignment or permission not in assignment.permissions)
        and event.organizer_id != user.id
        and "platform_admin" not in user.roles
    ):
        raise HTTPException(403, detail="Not assigned to event")
    return event


@router.get("/check-in/events", response_model=list[EventRead])
def checkin_events(user: User = Depends(current_user), db: Session = Depends(get_db)):
    ids = [
        a.event_id
        for a in db.scalars(select(StaffAssignment).where(StaffAssignment.user_id == user.id))
        if "check_in" in a.permissions
    ]
    return list(
        db.scalars(select(Event).where((Event.id.in_(ids)) | (Event.organizer_id == user.id))).all()
    )


@router.get("/check-in/events/{event_id}/attendees")
def event_attendance(
    event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    assigned_event(db, event_id, user)
    rows = db.execute(
        select(Ticket, TicketType.name, CheckInRecord.id)
        .join(TicketType, Ticket.ticket_type_id == TicketType.id)
        .outerjoin(
            CheckInRecord,
            (CheckInRecord.ticket_id == Ticket.id) & CheckInRecord.reversed_at.is_(None),
        )
        .where(Ticket.event_id == event_id)
        .order_by(Ticket.attendee_name, Ticket.id)
    ).all()
    return [
        {
            "id": str(ticket.id),
            "attendee_name": ticket.attendee_name,
            "attendee_email": ticket.attendee_email,
            "status": ticket.status,
            "ticket_type": name,
            "record_id": str(record_id) if record_id else None,
        }
        for ticket, name, record_id in rows
    ]


@router.post("/check-in/events/{event_id}/tickets/{ticket_id}/admit")
async def manual_admission(
    event_id: UUID,
    ticket_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    assigned_event(db, event_id, user)
    ticket = db.get(Ticket, UUID(str(ticket_id)))
    if not ticket or ticket.event_id != event_id:
        raise HTTPException(404, detail="Ticket not found for this event")
    return await admit(event_id, ticket.qr_credential, "manual", user, db)


@router.post("/check-in/events/{event_id}/validate")
def validate_admission(
    event_id: UUID,
    qr_payload: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    from app.core.security import parse_qr_credential

    event = assigned_event(db, event_id, user)
    event = db.scalar(
        select(Event)
        .where(Event.id == event.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    ticket_id = parse_qr_credential(qr_payload)
    if not ticket_id:
        return {
            "result": "invalid",
            "reason": "CAMPAIGN_QR_NOT_ADMISSION"
            if not qr_payload.startswith("ticket.")
            else "INVALID_QR",
        }
    ticket = db.get(Ticket, UUID(str(ticket_id)))
    if not ticket or ticket.event_id != event_id:
        return {"result": "wrong_event"}
    if event.status in ("cancelled", "suspended"):
        return {"result": "cancelled", "reason": "EVENT_UNAVAILABLE"}
    mapping = {
        TicketStatus.CANCELLED: "cancelled",
        TicketStatus.REFUNDED: "refunded",
        TicketStatus.CHECKED_IN: "already_used",
    }
    return {
        "result": mapping.get(ticket.status, "valid"),
        "ticket_id": str(ticket.id),
        "attendee_name": ticket.attendee_name,
    }


@router.post("/check-in/events/{event_id}/admit")
async def admit(
    event_id: UUID,
    qr_payload: str,
    device_id: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    from app.core.security import parse_qr_credential

    event = assigned_event(db, event_id, user)
    event = db.scalar(
        select(Event)
        .where(Event.id == event.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if event.status in ("cancelled", "suspended"):
        raise HTTPException(409, detail="Event is cancelled or suspended; admission is closed")
    ticket_id = parse_qr_credential(qr_payload)
    if not ticket_id:
        raise HTTPException(409, detail="Invalid admission QR")
    ticket = db.scalar(
        select(Ticket)
        .where(Ticket.id == UUID(str(ticket_id)))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not ticket or ticket.event_id != event.id:
        raise HTTPException(409, detail="Ticket is for another event")
    if ticket.status != TicketStatus.VALID:
        raise HTTPException(409, detail=f"Ticket status is {ticket.status}")
    record = CheckInRecord(
        ticket_id=ticket.id, event_id=event.id, staff_user_id=user.id, device_id=device_id
    )
    ticket.status = TicketStatus.CHECKED_IN
    db.add(record)
    db.flush()
    audit(
        db,
        user,
        event,
        "ticket.checked_in",
        "ticket",
        ticket.id,
        f"Checked in {ticket.attendee_name}",
    )
    db.commit()
    return {
        "result": "valid",
        "record_id": str(record.id),
        "ticket_id": str(ticket.id),
        "attendee_name": ticket.attendee_name,
    }


@router.post("/check-in/records/{record_id}/reverse")
def reverse_checkin(
    record_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    record = db.get(CheckInRecord, record_id)
    if not record:
        raise HTTPException(404, detail="Active check-in not found")
    event = assigned_event(db, record.event_id, user, "reverse_check_in")
    event = db.scalar(
        select(Event)
        .where(Event.id == event.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    ticket = db.scalar(select(Ticket).where(Ticket.id == record.ticket_id).with_for_update())
    db.refresh(record)
    if record.reversed_at or ticket.status != TicketStatus.CHECKED_IN:
        raise HTTPException(409, detail="Check-in is no longer active; refresh attendees")
    record.reversed_at = datetime.now(UTC)
    record.reversed_by = user.id
    ticket.status = TicketStatus.CANCELLED if event.status == "cancelled" else TicketStatus.VALID
    audit(db, user, event, "ticket.check_in_reversed", "ticket", ticket.id, "Check-in reversed")
    db.commit()
    return {"status": "reversed"}


@router.get("/check-in/events/{event_id}/summary")
def checkin_summary(
    event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    from sqlalchemy import func

    assigned_event(db, event_id, user)
    registered = db.scalar(select(func.count(Ticket.id)).where(Ticket.event_id == event_id)) or 0
    checked_in = (
        db.scalar(
            select(func.count(Ticket.id)).where(
                Ticket.event_id == event_id, Ticket.status == TicketStatus.CHECKED_IN
            )
        )
        or 0
    )
    return {"registered": registered, "checked_in": checked_in, "absent": registered - checked_in}


@router.get("/events/{event_id}/calendar.ics")
def calendar_export(event_id: UUID, db: Session = Depends(get_db)):
    event = db.get(Event, event_id)
    if not event or event.status == "draft" or event.visibility == "private":
        raise HTTPException(404, detail="Event not found")
    venue = db.get(Venue, event.venue_id)
    status = "CANCELLED" if event.status == "cancelled" else "CONFIRMED"

    def escape(value: str) -> str:
        return (
            value.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")
        )

    content = "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//BiletFlow//EN",
            "BEGIN:VEVENT",
            f"UID:event-{event.id}@biletflow.kz",
            f"DTSTAMP:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART:{event.starts_at.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{event.ends_at.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}",
            f"SUMMARY:{escape(event.title)}",
            f"DESCRIPTION:{escape(event.description)}",
            f"LOCATION:{escape(venue.name + ', ' + venue.address)}",
            f"STATUS:{status}",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
    )
    return Response(
        content,
        media_type="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="event-{event.id}.ics"'},
    )


@router.patch("/events/{event_id}/ticket-types/{ticket_type_id}", response_model=TicketTypeRead)
def update_draft_ticket_type(
    event_id: UUID,
    ticket_type_id: UUID,
    data: TicketTypeCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    event = db.scalar(
        select(Event)
        .where(Event.id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not event:
        raise HTTPException(404, detail="Event not found")
    if event.organizer_id != user.id and "platform_admin" not in user.roles:
        raise HTTPException(403, detail="Only the organizer or administrator can edit ticket types")
    item = db.scalar(
        select(TicketType)
        .where(TicketType.id == ticket_type_id, TicketType.event_id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not item:
        raise HTTPException(404, detail="Ticket type not found")
    if event.status != "draft" or item.sold_count or item.reserved_count or item.refunded_count:
        raise HTTPException(409, detail="Only unsold draft ticket types can be edited")
    if data.kind == "paid" and not event.refund_policy.strip():
        raise HTTPException(422, detail="Paid events require a refund policy")

    def utc(v):
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    if utc(data.sales_start) < utc(event.registration_opens_at) or utc(data.sales_end) > utc(
        event.registration_closes_at
    ):
        raise HTTPException(422, detail="Ticket sales must fit within the registration period")
    other_quantity = sum(
        (t.quantity if not t.is_hidden else t.sold_count + t.reserved_count)
        for t in event.ticket_types
        if t.id != item.id
    )
    if not item.is_hidden and other_quantity + data.quantity > event.capacity:
        raise HTTPException(422, detail="Ticket quantities exceed event capacity")

    def value(v):
        if isinstance(v, datetime):
            return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)
        return v

    if any(value(getattr(item, k)) != value(v) for k, v in data.model_dump().items()):
        from app.events.paid_applications import invalidate_review

        invalidate_review(db, event, user)
        for k, v in data.model_dump().items():
            setattr(item, k, v)
        audit(
            db,
            user,
            event,
            "ticket_type.updated",
            "ticket_type",
            item.id,
            "Updated draft ticket configuration",
        )
    db.commit()
    db.refresh(item)
    return item
