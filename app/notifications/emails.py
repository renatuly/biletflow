"""Transactional email templates. Every builder returns a ready-to-send OutgoingEmail."""

from html import escape
from urllib.parse import quote

from app.core.config import get_settings
from app.core.email import Attachment, OutgoingEmail
from app.events.models import Event, Venue
from app.orders.models import Order
from app.tickets.models import Ticket, TicketType
from app.tickets.service import admission_qr_png, format_event_time, render_ticket_pdf
from app.users.models import User


def kzt(minor: int) -> str:
    major, cents = divmod(minor, 100)
    return f"{major:,} KZT".replace(",", " ") if not cents else f"{minor / 100:,.2f} KZT"


def _layout(title: str, body_html: str) -> str:
    return f"""<!doctype html>
<html lang="en"><body style="margin:0;background:#f5f5f7;font-family:Arial,Helvetica,sans-serif;color:#1d1d1f">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:12px">
<tr><td style="padding:24px 32px;border-bottom:1px solid #e5e5ea;font-weight:bold;font-size:18px;color:#5b3df5">BiletFlow</td></tr>
<tr><td style="padding:32px">
<h1 style="margin:0 0 16px;font-size:22px">{escape(title)}</h1>
{body_html}
</td></tr>
<tr><td style="padding:16px 32px;border-top:1px solid #e5e5ea;font-size:12px;color:#6e6e73">
You received this email because of activity on your BiletFlow account.
Payments in this academic demonstration are simulated; no real money is charged.
</td></tr></table></td></tr></table></body></html>"""


def _button(url: str, label: str) -> str:
    return (
        f'<p style="margin:24px 0"><a href="{escape(url)}" style="background:#5b3df5;color:#ffffff;'
        f'padding:12px 20px;border-radius:8px;text-decoration:none;display:inline-block">'
        f"{escape(label)}</a></p>"
        f'<p style="font-size:12px;color:#6e6e73">If the button does not work, open this link:'
        f'<br><a href="{escape(url)}">{escape(url)}</a></p>'
    )


def _greeting(name: str | None) -> str:
    return f"Hello {name}," if name else "Hello,"


def verification_email(user: User, token: str) -> OutgoingEmail:
    settings = get_settings()
    url = f"{settings.frontend_url}/verify-email?token={quote(token)}"
    hours = settings.app_email_verification_hours
    text = (
        f"{_greeting(user.full_name)}\n\nConfirm your BiletFlow email address by opening this link:"
        f"\n{url}\n\nThe link expires in {hours} hours. If you did not create an account, "
        "ignore this message."
    )
    html = _layout(
        "Confirm your email address",
        f"<p>{escape(_greeting(user.full_name))}</p>"
        "<p>Confirm your email address to finish setting up your BiletFlow account.</p>"
        + _button(url, "Verify email")
        + f"<p>The link expires in {hours} hours. If you did not create an account, "
        "ignore this message.</p>",
    )
    return OutgoingEmail(user.email, "Verify your BiletFlow email", text, html)


def password_reset_email(user: User, token: str) -> OutgoingEmail:
    settings = get_settings()
    url = f"{settings.frontend_url}/reset-password?token={quote(token)}"
    minutes = settings.app_password_reset_minutes
    text = (
        f"{_greeting(user.full_name)}\n\nWe received a request to reset your BiletFlow password."
        f"\nChoose a new password here:\n{url}\n\nThe link expires in {minutes} minutes and can "
        "be used once. If you did not request a reset, you can ignore this email."
    )
    html = _layout(
        "Reset your password",
        f"<p>{escape(_greeting(user.full_name))}</p>"
        "<p>We received a request to reset your BiletFlow password.</p>"
        + _button(url, "Choose a new password")
        + f"<p>The link expires in {minutes} minutes and can be used once. If you did not "
        "request a reset, you can ignore this email; your password will not change.</p>",
    )
    return OutgoingEmail(user.email, "Reset your BiletFlow password", text, html)


def password_changed_email(user: User) -> OutgoingEmail:
    url = f"{get_settings().frontend_url}/forgot-password"
    text = (
        f"{_greeting(user.full_name)}\n\nYour BiletFlow password was just changed. If this was "
        f"not you, reset your password immediately: {url}"
    )
    html = _layout(
        "Your password was changed",
        f"<p>{escape(_greeting(user.full_name))}</p>"
        "<p>Your BiletFlow password was just changed. If this was not you, reset it now.</p>"
        + _button(url, "Reset password"),
    )
    return OutgoingEmail(user.email, "Your BiletFlow password was changed", text, html)


def order_confirmation_email(
    order: Order,
    event: Event,
    venue: Venue,
    tickets: list[tuple[Ticket, TicketType]],
) -> OutgoingEmail:
    """Registration/purchase confirmation with every ticket attached as a printable PDF."""
    settings = get_settings()
    order_url = f"{settings.frontend_url}/orders/{order.id}"
    when = format_event_time(event)
    place = f"{venue.name}, {venue.address}, {venue.city}"
    free = order.total_minor == 0
    subject = f"{'Registration' if free else 'Order'} confirmed: {event.title}"
    attachments: list[Attachment] = []
    ticket_rows_html = []
    ticket_lines = []
    for index, (ticket, ticket_type) in enumerate(tickets, start=1):
        cid = f"ticket-qr-{ticket.id}"
        attachments.append(
            Attachment(
                f"ticket-{index}-qr.png", admission_qr_png(ticket.qr_credential), "image/png", cid
            )
        )
        attachments.append(
            Attachment(
                f"ticket-{index}-{ticket.id}.pdf",
                render_ticket_pdf(ticket, event, venue, ticket_type),
                "application/pdf",
            )
        )
        seat = (
            f" · Section {ticket.section} Row {ticket.row} Seat {ticket.seat}"
            if ticket.seat
            else ""
        )
        ticket_lines.append(f"- {ticket_type.name}{seat} · {ticket.attendee_name} · ID {ticket.id}")
        ticket_rows_html.append(
            '<tr><td style="padding:16px 0;border-top:1px solid #e5e5ea">'
            f"<strong>{escape(ticket_type.name)}</strong>{escape(seat)}<br>"
            f'<span style="font-size:12px;color:#6e6e73">{escape(ticket.attendee_name)} · '
            f"Ticket ID {ticket.id}</span><br>"
            f'<img src="cid:{cid}" width="180" height="180" alt="Admission QR code for ticket '
            f'{ticket.id}" style="margin-top:8px"></td></tr>'
        )
    totals = [("Subtotal", kzt(order.subtotal_minor))]
    if order.discount_minor:
        totals.append(("Discount", f"-{kzt(order.discount_minor)}"))
    if order.processing_fee_minor:
        totals.append(("Processing fee", kzt(order.processing_fee_minor)))
    totals.append(("Total", kzt(order.total_minor)))
    totals_html = "".join(
        f'<tr><td style="padding:4px 0">{label}</td><td align="right">{escape(value)}</td></tr>'
        for label, value in totals
    )
    text = "\n".join(
        [
            f"Hello {order.attendee_name},",
            "",
            f"Your {'registration' if free else 'order'} for {event.title} is confirmed.",
            f"When: {when}",
            f"Where: {place}",
            "",
            "Tickets (PDFs attached; each QR code admits once):",
            *ticket_lines,
            "",
            *(f"{label}: {value}" for label, value in totals),
            "",
            f"View your order: {order_url}",
            f"Order reference: {order.id}",
        ]
    )
    html = _layout(
        subject,
        f"<p>Hello {escape(order.attendee_name)},</p>"
        f"<p>Your {'registration' if free else 'order'} for <strong>{escape(event.title)}"
        "</strong> is confirmed.</p>"
        f"<p><strong>When:</strong> {escape(when)}<br><strong>Where:</strong> {escape(place)}</p>"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        + "".join(ticket_rows_html)
        + "</table>"
        '<p style="font-size:12px;color:#6e6e73">Printable PDF tickets are attached. Digital and '
        "printed copies share one ticket ID and admit only once.</p>"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin-top:16px;border-top:1px solid #e5e5ea">{totals_html}</table>'
        + _button(order_url, "View order and tickets")
        + f'<p style="font-size:12px;color:#6e6e73">Order reference: {order.id}</p>',
    )
    return OutgoingEmail(order.attendee_email, subject, text, html, attachments)


def payment_failed_email(order: Order, event: Event) -> OutgoingEmail:
    url = f"{get_settings().frontend_url}/events/{event.id}"
    text = (
        f"Hello {order.attendee_name},\n\nThe payment for your {event.title} order did not go "
        f"through, so no tickets were issued. You can try again here: {url}"
    )
    html = _layout(
        "Payment not completed",
        f"<p>Hello {escape(order.attendee_name)},</p>"
        f"<p>The payment for your <strong>{escape(event.title)}</strong> order did not go "
        "through, so no tickets were issued and your reserved tickets were released.</p>"
        + _button(url, "Try again"),
    )
    return OutgoingEmail(order.attendee_email, f"Payment failed: {event.title}", text, html)


def refund_email(order: Order, event: Event, amount_minor: int) -> OutgoingEmail:
    text = (
        f"Hello {order.attendee_name},\n\nYour order for {event.title} was refunded "
        f"({kzt(amount_minor)}). The associated tickets are no longer valid for admission.\n"
        f"Order reference: {order.id}"
    )
    html = _layout(
        "Refund completed",
        f"<p>Hello {escape(order.attendee_name)},</p>"
        f"<p>Your order for <strong>{escape(event.title)}</strong> was refunded "
        f"(<strong>{escape(kzt(amount_minor))}</strong>). The associated tickets are no longer "
        f"valid for admission.</p><p>Order reference: {order.id}</p>",
    )
    return OutgoingEmail(order.attendee_email, f"Refund completed: {event.title}", text, html)


def event_cancelled_email(name: str, address: str, event: Event) -> OutgoingEmail:
    settings = get_settings()
    calendar_url = f"{settings.frontend_url}/events/{event.id}"
    text = (
        f"Hello {name},\n\n{event.title} ({format_event_time(event)}) has been cancelled by the "
        "organizer. Your tickets are no longer valid. The organizer will contact you about "
        f"refunds according to the event refund policy: {event.refund_policy}\n{calendar_url}"
    )
    html = _layout(
        "Event cancelled",
        f"<p>Hello {escape(name)},</p>"
        f"<p><strong>{escape(event.title)}</strong> ({escape(format_event_time(event))}) has been "
        "cancelled by the organizer. Your tickets are no longer valid.</p>"
        f"<p>Refund policy: {escape(event.refund_policy)}</p>"
        + _button(calendar_url, "View event"),
    )
    return OutgoingEmail(address, f"Event cancelled: {event.title}", text, html)
