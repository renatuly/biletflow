"""Business rules for the tickets domain."""

import io
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import qrcode
from qrcode.constants import ERROR_CORRECT_M
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

from app.events.models import Event, Venue
from app.tickets.models import Ticket, TicketType


def local_time(value: datetime, time_zone: str) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
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


@lru_cache(maxsize=1)
def ticket_fonts():
    directory = Path(__file__).resolve().parents[1] / "assets/fonts/noto_sans"
    for name, filename in [
        ("BiletFlowSans", "NotoSans-Regular.ttf"),
        ("BiletFlowSans-Bold", "NotoSans-Bold.ttf"),
    ]:
        pdfmetrics.registerFont(TTFont(name, str(directory / filename)))
    pdfmetrics.registerFontFamily(
        "BiletFlowSans", normal="BiletFlowSans", bold="BiletFlowSans-Bold"
    )


def render_ticket_pdf(ticket: Ticket, event: Event, venue: Venue, ticket_type: TicketType) -> bytes:
    """Unicode A4 admission ticket with flowing text and an indivisible QR block."""
    ticket_fonts()
    output = io.BytesIO()
    margin = 20 * mm
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin + 10 * mm,
        title=f"BiletFlow Ticket {ticket.id}",
    )
    body = ParagraphStyle(
        "TicketBody",
        fontName="BiletFlowSans",
        fontSize=11,
        leading=16,
        spaceAfter=8,
        splitLongWords=True,
    )
    heading = ParagraphStyle(
        "TicketTitle",
        parent=body,
        fontName="BiletFlowSans-Bold",
        fontSize=19,
        leading=25,
        spaceAfter=16,
    )

    def paragraph(text, style=body):
        # User strings are text, never ReportLab markup or remote image URLs.
        return Paragraph(escape(str(text)).replace("\r", "").replace("\n", "<br/>"), style)

    story = [paragraph("BILETFLOW · ADMISSION TICKET"), paragraph(event.title, heading)]
    fields = [
        f"Date: {format_event_time(event)}",
        f"Venue: {venue.name}, {venue.address}, {venue.city}",
        f"Ticket type: {ticket_type.name}",
        f"Attendee: {ticket.attendee_name}",
    ]
    if ticket.seat:
        fields.append(
            f"Seat: Section {ticket.section or '-'} · Row {ticket.row or '-'} · Seat {ticket.seat}"
        )
    story.extend(paragraph(line) for line in fields)
    qr_size = 85 * mm
    story.append(
        KeepTogether(
            [
                Spacer(1, 4 * mm),
                paragraph(f"Ticket ID: {ticket.id}"),
                Image(
                    io.BytesIO(admission_qr_png(ticket.qr_credential)),
                    width=qr_size,
                    height=qr_size,
                    hAlign="LEFT",
                ),
                Spacer(1, 4 * mm),
                paragraph(
                    "Present this QR code at the entrance. "
                    "Digital and printed copies admit only once."
                ),
            ]
        )
    )

    def footer(page, doc):
        page.saveState()
        page.setFont("BiletFlowSans", 9)
        page.drawString(margin, margin, f"Status at issue: {ticket.status}")
        page.drawRightString(A4[0] - margin, margin, f"biletflow.kz · {doc.page}")
        page.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
