"""Email delivery diagnostics: shows why verification emails do not arrive.

Background delivery only logs failures, so a misconfigured provider fails silently for users.
These checks send synchronously and return the provider's real answer.

    python -m app.notifications.diagnostics you@example.com
"""

import sys

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr

from app.core import email as mail
from app.core.config import get_settings
from app.core.dependencies import require_role
from app.users.models import User, UserRole

router = APIRouter(tags=["admin email diagnostics"])


class TestEmail(BaseModel):
    to: EmailStr | None = None


def status() -> dict:
    settings = get_settings()
    return {
        "backend": settings.email_backend,
        "sender": settings.email_from_address,
        "problems": mail.config_problems(),
    }


def send_test(to: str) -> dict:
    message = mail.OutgoingEmail(
        to=to,
        subject="BiletFlow email delivery test",
        text="This test message confirms that BiletFlow can deliver email.",
        html="<p>This test message confirms that BiletFlow can deliver email.</p>",
    )
    try:
        reference = mail.send(message)
    except Exception as error:  # report the provider's answer instead of hiding it
        return {**status(), "to": to, "delivered": False, "detail": str(error)}
    return {
        **status(),
        "to": to,
        "delivered": reference not in ("memory", "console"),
        "detail": f"Accepted by {get_settings().email_backend} (reference {reference}). "
        "If it never arrives, check the recipient's spam folder and Brevo > Transactional > Logs.",
    }


@router.get("/admin/email/status")
def email_status(admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN))):
    return status()


@router.post("/admin/email/test")
def email_test(data: TestEmail, admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN))):
    return send_test(str(data.to or admin.email))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m app.notifications.diagnostics you@example.com")
    result = send_test(sys.argv[1])
    for key, value in result.items():
        print(f"{key}: {value}")
    raise SystemExit(0 if result["delivered"] else 1)
