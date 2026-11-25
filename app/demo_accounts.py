"""Provision the four standard demo accounts on any environment, including production.

Unlike ``app.dev_seed`` (which also creates sample events, orders and tickets and refuses
to run in production), this only creates or repairs login accounts, one per role:

    python -m app.demo_accounts                    # create missing accounts
    python -m app.demo_accounts --reset-passwords  # also reset existing demo passwords

The password comes from DEMO_ACCOUNTS_PASSWORD. In production it is required and must be
at least 12 characters; elsewhere it defaults to the local seed password. To provision a
host without shell access (e.g. Render free tier), set DEMO_ACCOUNTS_PASSWORD on the service:
the API then creates any missing accounts at startup without changing existing passwords.
"""

import argparse
import logging
import os
import sys

from sqlalchemy import select

from app import models  # noqa: F401 - registers all ORM metadata
from app.core.config import get_settings
from app.core.database import SessionLocal, create_schema
from app.core.security import hash_password
from app.events.models import OrganizerProfile
from app.users.models import User

logger = logging.getLogger("biletflow.demo_accounts")

LOCAL_PASSWORD = "demo1234"
# (email, full name, roles). Every role keeps "attendee" so the account can also buy tickets.
DEMO_ACCOUNTS = (
    ("admin@example.com", "Demo Platform Admin", ["attendee", "platform_admin"]),
    ("organizer@example.com", "Demo Organizer", ["attendee", "organizer"]),
    ("attendee@example.com", "Demo Attendee", ["attendee"]),
    ("staff@example.com", "Demo Event Admin", ["attendee", "event_admin"]),
)


def demo_password(required: bool) -> str | None:
    password = os.environ.get("DEMO_ACCOUNTS_PASSWORD", "").strip()
    if not password:
        if get_settings().app_env == "production":
            if required:
                raise SystemExit("Set DEMO_ACCOUNTS_PASSWORD (12+ characters) to seed production")
            return None
        return LOCAL_PASSWORD
    if get_settings().app_env == "production" and len(password) < 12:
        raise SystemExit("DEMO_ACCOUNTS_PASSWORD must be at least 12 characters in production")
    return password


def ensure_demo_accounts(password: str, reset_passwords: bool = False) -> list[str]:
    """Create missing demo accounts; returns a line per account describing what happened."""
    create_schema()
    report = []
    with SessionLocal() as db:
        for email, full_name, roles in DEMO_ACCOUNTS:
            user = db.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(
                    email=email,
                    full_name=full_name,
                    password_hash=hash_password(password),
                    roles=roles,
                    email_verified=True,
                    is_active=True,
                )
                db.add(user)
                action = "created"
            else:
                # Keep roles an administrator may have added; restore the demo role set.
                user.roles = [*user.roles, *(r for r in roles if r not in user.roles)]
                user.email_verified = True
                user.is_active = True
                if reset_passwords:
                    user.password_hash = hash_password(password)
                action = "password reset" if reset_passwords else "already present"
            db.flush()
            if "organizer" in roles and not db.scalar(
                select(OrganizerProfile).where(OrganizerProfile.user_id == user.id)
            ):
                # Same state as an approved organizer application: paid sales still need
                # the sandbox checklist and an administrator's paid-event review.
                db.add(
                    OrganizerProfile(
                        user_id=user.id,
                        contact_name=full_name,
                        organization_name="BiletFlow Demo Events",
                        contact_phone="+77000000000",
                    )
                )
            report.append(f"{email} ({', '.join(roles)}): {action}")
        db.commit()
    return report


def provision_on_startup() -> None:
    """Called by the API at startup; a no-op unless DEMO_ACCOUNTS_PASSWORD is set."""
    if not os.environ.get("DEMO_ACCOUNTS_PASSWORD", "").strip():
        return
    try:
        password = demo_password(required=True)
        for line in ensure_demo_accounts(password):
            logger.info("Demo account %s", line)
    except SystemExit as error:
        logger.error("Demo accounts not provisioned: %s", error)
    except Exception:
        logger.exception("Demo account provisioning failed")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--reset-passwords",
        action="store_true",
        help="also set existing demo accounts to DEMO_ACCOUNTS_PASSWORD",
    )
    args = parser.parse_args(argv)
    password = demo_password(required=True)
    for line in ensure_demo_accounts(password, reset_passwords=args.reset_passwords):
        print(line)
    if password == LOCAL_PASSWORD:
        print(f"Password: {LOCAL_PASSWORD} (local default)")
    else:
        print("Password: the value of DEMO_ACCOUNTS_PASSWORD")
    print(
        "Event Admin: assign staff@example.com to an event from the organizer's "
        "event page (Event staff) to enable mobile check-in."
    )


if __name__ == "__main__":
    main(sys.argv[1:])
