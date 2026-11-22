"""Transactional email delivery.

Messages are fully rendered inside the request (while the database session is open) and
delivered afterwards with FastAPI ``BackgroundTasks`` so SMTP latency never blocks checkout.
"""

import base64
import json
import logging
import re
import smtplib
import ssl
import urllib.error
import urllib.request
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


BREVO_URL = "https://api.brevo.com/v3/smtp/email"
_CID_IMG = re.compile(r'<img[^>]*src="cid:[^"]+"[^>]*>')


def _brevo_payload(email: OutgoingEmail) -> dict:
    """Brevo's API has no inline (cid:) images, so those become ordinary attachments."""
    settings = get_settings()
    html = email.html
    if any(a.content_id for a in email.attachments):
        html = _CID_IMG.sub(
            '<div style="margin-top:8px;font-size:12px;color:#6e6e73">'
            "QR code attached to this email</div>",
            html,
        )
    payload: dict = {
        "sender": {"name": settings.email_from_name, "email": settings.email_from_address},
        "to": [{"email": email.to}],
        "subject": email.subject,
        "htmlContent": html,
        "textContent": email.text,
    }
    if settings.email_reply_to:
        payload["replyTo"] = {"email": settings.email_reply_to}
    if email.attachments:
        payload["attachment"] = [
            {"name": a.filename, "content": base64.b64encode(a.content).decode("ascii")}
            for a in email.attachments
        ]
    return payload


USER_AGENT = "BiletFlow-API/0.1 (+https://github.com/biletflow)"
DEFAULT_SENDER = "no-reply@biletflow.kz"


def _brevo_hint(status: int, detail: str) -> str:
    """Translate Brevo's common rejections into the setting that has to change."""
    text = detail.lower()
    if status == 401 and "ip" in text:
        return (
            " Hint: Brevo blocks API calls from unrecognised IP addresses. Render's outbound "
            "IPs change, so disable 'Authorised IPs' (Brevo > Security > Authorised IPs) "
            "or add every Render outbound IP."
        )
    if status == 401:
        return (
            " Hint: BREVO_API_KEY must be a v3 API key (starts with 'xkeysib-'), not an SMTP "
            "key ('xsmtpsib-'). Create one under Brevo > SMTP & API > API Keys."
        )
    if status == 400 and "sender" in text:
        return (
            " Hint: EMAIL_FROM_ADDRESS must be a verified sender (Brevo > Senders, "
            "Domains & Dedicated IPs)."
        )
    if status == 403 and ("not activated" in text or "permission" in text):
        return " Hint: the Brevo account's transactional email service is not activated yet."
    return ""


def _send_brevo(email: OutgoingEmail) -> str:
    """Send through Brevo and return its messageId; raises with a diagnostic on failure."""
    settings = get_settings()
    request = urllib.request.Request(
        BREVO_URL,
        data=json.dumps(_brevo_payload(email)).encode("utf-8"),
        method="POST",
        headers={
            "api-key": settings.brevo_api_key or "",
            "accept": "application/json",
            "content-type": "application/json",
            # Python's default "Python-urllib" agent can be rejected by Brevo's edge.
            "user-agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.smtp_timeout_seconds) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(
            f"Brevo API returned {error.code}: {detail}.{_brevo_hint(error.code, detail)}"
        ) from error
    try:
        message_id = json.loads(body or b"{}").get("messageId") or ""
    except (ValueError, AttributeError):
        message_id = ""
    if not message_id:
        raise RuntimeError(f"Brevo API accepted the request without a messageId: {body[:200]!r}")
    return str(message_id)


def config_problems() -> list[str]:
    """Misconfigurations that make delivery fail; no secret values are included."""
    settings = get_settings()
    problems = []
    sender = (settings.email_from_address or "").strip()
    if settings.email_backend in ("console", "memory"):
        problems.append(
            f"EMAIL_BACKEND={settings.email_backend}: messages are not delivered to inboxes."
        )
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", sender):
        problems.append("EMAIL_FROM_ADDRESS is empty or not an email address.")
    elif settings.email_backend == "brevo" and sender == DEFAULT_SENDER:
        problems.append(
            f"EMAIL_FROM_ADDRESS is the default {DEFAULT_SENDER}; set it to a sender "
            "verified in Brevo."
        )
    if settings.email_backend == "brevo":
        key = (settings.brevo_api_key or "").strip()
        if not key:
            problems.append("BREVO_API_KEY is not set.")
        elif key.startswith("xsmtpsib-"):
            problems.append(
                "BREVO_API_KEY is an SMTP key (xsmtpsib-); the HTTPS API needs a v3 API key "
                "(xkeysib-)."
            )
        elif not key.startswith("xkeysib-"):
            problems.append("BREVO_API_KEY does not look like a Brevo v3 API key (xkeysib-...).")
        elif key != (settings.brevo_api_key or ""):
            problems.append("BREVO_API_KEY has leading or trailing whitespace.")
    if settings.email_backend == "smtp" and not settings.smtp_host:
        problems.append("SMTP_HOST is not set.")
    return problems


def send(email: OutgoingEmail) -> str:
    """Send one message synchronously and raise on failure. Returns a delivery reference."""
    settings = get_settings()
    if settings.email_backend == "memory":
        outbox.append(email)
        return "memory"
    if settings.email_backend == "console":
        logger.info("Email to %s: %s\n%s", email.to, email.subject, email.text)
        return "console"
    if settings.email_backend == "brevo":
        return _send_brevo(email)
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
    return "smtp"


def deliver(email: OutgoingEmail) -> None:
    """Send one message synchronously. Failures are logged, never raised to the caller."""
    try:
        reference = send(email)
        if reference not in ("memory", "console"):
            logger.info("Email to %s accepted (%s): %s", email.to, reference, email.subject)
    except Exception:
        logger.exception("Email delivery to %s failed: %s", email.to, email.subject)


def queue(background: BackgroundTasks, *emails: OutgoingEmail) -> None:
    for email in emails:
        background.add_task(deliver, email)
