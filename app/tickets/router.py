import io
from datetime import UTC, datetime
from uuid import UUID

import qrcode
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import current_user
from app.events.models import Event, StaffAssignment, Venue
from app.events.service import audit, require_event_manager
from app.tickets.models import CheckInRecord, Ticket, TicketStatus, TicketType
from app.tickets.schemas import TicketRead, TicketTypeCreate, TicketTypeRead
from app.users.models import User

router = APIRouter(tags=["tickets"])


@router.get("/events/{event_id}/ticket-types", response_model=list[TicketTypeRead])
def list_ticket_types(event_id: UUID, db: Session = Depends(get_db)):
    event = db.get(Event, event_id)
    if not event or event.status != "published":
        raise HTTPException(404, detail="Event not found")
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
    if event.status == "cancelled":
        raise HTTPException(409, detail="Cannot add tickets to cancelled event")
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
    event = require_event_manager(db, event_id, user)
    item = db.get(TicketType, ticket_type_id)
    if not item or item.event_id != event.id:
        raise HTTPException(404, detail="Ticket type not found")
    item.is_hidden = True
    audit(db, user, event, "ticket_type.hidden", "ticket_type", item.id, "Ticket type hidden")
    db.commit()
    db.refresh(item)
    return item


@router.get("/tickets", response_model=list[TicketRead])
async def my_tickets(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return list(
        db.scalars(
            select(Ticket).where(Ticket.owner_id == user.id).order_by(Ticket.created_at.desc())
        ).all()
    )


@router.get("/tickets/{ticket_id}", response_model=TicketRead)
def read_ticket(ticket_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ticket = db.get(Ticket, UUID(ticket_id))
    if not ticket:
        raise HTTPException(404, detail="Ticket not found")
    event = db.get(Event, ticket.event_id)
    if (
        ticket.owner_id != user.id
        and event.organizer_id != user.id
        and "platform_admin" not in user.roles
    ):
        raise HTTPException(403, detail="Not authorized")
    return ticket


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


@router.get("/tickets/{ticket_id}/qr")
def ticket_qr(ticket_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ticket, _ = authorized_ticket(db, ticket_id, user)
    image = qrcode.make(ticket.qr_credential)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return Response(stream.getvalue(), media_type="image/png")


@router.get("/tickets/{ticket_id}/pdf")
async def ticket_pdf(
    ticket_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    ticket, event = authorized_ticket(db, ticket_id, user)
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


def assigned_event(db: Session, event_id: UUID, user: User) -> Event:
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
        not assignment and event.organizer_id != user.id and "platform_admin" not in user.roles
    ):
        raise HTTPException(403, detail="Not assigned to event")
    return event


@router.get("/check-in/events")
def checkin_events(user: User = Depends(current_user), db: Session = Depends(get_db)):
    ids = select(StaffAssignment.event_id).where(StaffAssignment.user_id == user.id)
    return list(
        db.scalars(select(Event).where((Event.id.in_(ids)) | (Event.organizer_id == user.id))).all()
    )


@router.post("/check-in/events/{event_id}/validate")
def validate_admission(
    event_id: UUID,
    qr_payload: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    from app.core.security import parse_qr_credential

    assigned_event(db, event_id, user)
    ticket_id = parse_qr_credential(qr_payload)
    if not ticket_id:
        return {
            "result": "invalid",
            "reason": "CAMPAIGN_QR_NOT_ADMISSION"
            if not qr_payload.startswith("ticket.")
            else "INVALID_QR",
        }
    ticket = db.get(Ticket, UUID(ticket_id))
    if not ticket or ticket.event_id != event_id:
        return {"result": "wrong_event"}
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
    ticket_id = parse_qr_credential(qr_payload)
    if not ticket_id:
        raise HTTPException(409, detail="Invalid admission QR")
    ticket = db.scalar(select(Ticket).where(Ticket.id == UUID(ticket_id)).with_for_update())
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
    if not record or record.reversed_at:
        raise HTTPException(404, detail="Active check-in not found")
    event = assigned_event(db, record.event_id, user)
    ticket = db.scalar(select(Ticket).where(Ticket.id == record.ticket_id).with_for_update())
    record.reversed_at = datetime.now(UTC)
    record.reversed_by = user.id
    ticket.status = TicketStatus.VALID
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
