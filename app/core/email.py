"""Transactional email delivery.

Messages are fully rendered inside the request (while the database session is open) and
delivered afterwards with FastAPI ``BackgroundTasks`` so SMTP latency never blocks checkout.
"""

import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from fastapi import BackgroundTasks

from app.core.config import get_settings

logger = logging.getLogger("biletflow.email")


@dataclass(frozen=True)
class Attachment:
    filename: str
    content: bytes
    mime_type: str
    # Inline attachments are referenced from the HTML body as cid:<content_id>.
    content_id: str | None = None


@dataclass
class OutgoingEmail:
    to: str
    subject: str
    text: str
    html: str
    attachments: list[Attachment] = field(default_factory=list)


# Messages captured by the "memory" backend; used by automated tests.
outbox: list[OutgoingEmail] = []


def _build_message(email: OutgoingEmail) -> EmailMessage:
    settings = get_settings()
    message = EmailMessage()
    message["From"] = formataddr((settings.email_from_name, settings.email_from_address))
    message["To"] = email.to
    message["Subject"] = email.subject
    message["Message-ID"] = make_msgid(domain=settings.email_from_address.split("@")[-1])
    if settings.email_reply_to:
        message["Reply-To"] = settings.email_reply_to
    message.set_content(email.text)
    message.add_alternative(email.html, subtype="html")
    html_part = message.get_payload()[-1]
    for attachment in email.attachments:
        maintype, subtype = attachment.mime_type.split("/", 1)
        if attachment.content_id:
            html_part.add_related(
                attachment.content,
                maintype=maintype,
                subtype=subtype,
                cid=f"<{attachment.content_id}>",
                filename=attachment.filename,
            )
        else:
            message.add_attachment(
                attachment.content,
                maintype=maintype,
                subtype=subtype,
                filename=attachment.filename,
            )
    return message


def deliver(email: OutgoingEmail) -> None:
    """Send one message synchronously. Failures are logged, never raised to the caller."""
    settings = get_settings()
    try:
        if settings.email_backend == "memory":
            outbox.append(email)
            return
        if settings.email_backend == "console":
            logger.info("Email to %s: %s\n%s", email.to, email.subject, email.text)
            return
        message = _build_message(email)
        context = ssl.create_default_context()
        if settings.smtp_security == "ssl":
            server = smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=settings.smtp_timeout_seconds,
                context=context,
            )
        else:
            server = smtplib.SMTP(
                settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds
            )
        with server:
            if settings.smtp_security == "starttls":
                server.starttls(context=context)
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password or "")
            server.send_message(message)
    except Exception:
        logger.exception("Email delivery to %s failed: %s", email.to, email.subject)


def queue(background: BackgroundTasks, *emails: OutgoingEmail) -> None:
    for email in emails:
        background.add_task(deliver, email)
