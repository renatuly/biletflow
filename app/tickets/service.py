"""Business rules for the tickets domain."""

import io
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import qrcode
from qrcode.constants import ERROR_CORRECT_M
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.events.models import Event, Venue
from app.tickets.models import Ticket, TicketType


def local_time(value: datetime, time_zone: str) -> datetime:
    try:
        return value.astimezone(ZoneInfo(time_zone))
    except ZoneInfoNotFoundError:
        return value


def format_event_time(event: Event) -> str:
    start = local_time(event.starts_at, event.time_zone)
    end = local_time(event.ends_at, event.time_zone)
    end_format = "%H:%M" if start.date() == end.date() else "%d %b %Y, %H:%M"
    return f"{start:%d %b %Y, %H:%M} – {end.strftime(end_format)} ({event.time_zone})"


def admission_qr_png(credential: str) -> bytes:
    """Plain black-on-white QR; stays scannable when printed in grayscale."""
    code = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=10, border=4)
    code.add_data(credential)
    code.make(fit=True)
    stream = io.BytesIO()
    code.make_image(fill_color="black", back_color="white").save(stream, format="PNG")
    return stream.getvalue()


def render_ticket_pdf(ticket: Ticket, event: Event, venue: Venue, ticket_type: TicketType) -> bytes:
    """Print-optimized A4 ticket built from the canonical ticket record."""
    output = io.BytesIO()
    page = canvas.Canvas(output, pagesize=A4)
    width, height = A4
    margin = 20 * mm
    page.setTitle(f"BiletFlow Ticket {ticket.id}")
    page.setFont("Helvetica-Bold", 10)
    page.drawString(margin, height - margin, "BILETFLOW · ADMISSION TICKET")
    page.setFont("Helvetica-Bold", 20)
    page.drawString(margin, height - margin - 30, event.title[:60])
    page.setFont("Helvetica", 12)
    lines = [
        f"Date: {format_event_time(event)}",
        f"Venue: {venue.name}, {venue.address}, {venue.city}",
        f"Ticket type: {ticket_type.name}",
        f"Attendee: {ticket.attendee_name}",
    ]
    if ticket.seat:
        lines.append(
            f"Seat: Section {ticket.section or '-'} · Row {ticket.row or '-'} · Seat {ticket.seat}"
        )
    lines.append(f"Ticket ID: {ticket.id}")
    for index, line in enumerate(lines):
        page.drawString(margin, height - margin - 62 - index * 20, line)
    qr_size = 85 * mm
    qr_top = height - margin - 62 - len(lines) * 20 - 20
    page.drawImage(
        ImageReader(io.BytesIO(admission_qr_png(ticket.qr_credential))),
        margin,
        qr_top - qr_size,
        width=qr_size,
        height=qr_size,
    )
    page.setFont("Helvetica", 9)
    page.drawString(
        margin,
        qr_top - qr_size - 16,
        "Present this QR code at the entrance. Digital and printed copies admit only once.",
    )
    page.drawString(margin, margin, f"Status at issue: {ticket.status}")
    page.drawRightString(width - margin, margin, "biletflow.kz")
    page.showPage()
    page.save()
    return output.getvalue()
