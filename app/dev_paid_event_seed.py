"""Create additive paid-event review samples, preserving all existing decisions.

Run: python -m app.dev_paid_event_seed
All records are labelled demo data; payments and decisions are simulated fixtures.
"""

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from app import models  # noqa: F401
from app.core.config import get_settings
from app.core.database import SessionLocal, create_schema
from app.core.security import hash_password
from app.events.models import Event, OrganizerProfile, Venue
from app.events.paid_applications import ReviewDecision, decide, invalidate_review, submit
from app.events.service import audit
from app.tickets.models import TicketType
from app.users.models import User

PASSWORD = "DemoPass123!"
ORGANIZER_EMAIL = "paid.organizer.demo@example.com"
ADMIN_EMAIL = "admin.demo@example.com"
SAMPLES = (
    ("jazz", "[Demo] Almaty Evening Jazz", "music", "Almaty", "pending_review", 250000, 120),
    ("tech", "[Demo] Astana Tech Forum", "tech", "Astana", "pending_review", 800000, 200),
    ("market", "[Demo] Food and Craft Market", "food", "Almaty", "approved", 150000, 180),
    ("comedy", "[Demo] Stand-up Comedy Night", "other", "Astana", "rejected", 300000, 100),
    ("art", "[Demo] Contemporary Art Workshop", "art", "Almaty", "changes_required", 450000, 40),
)


def ensure_user(db, email, name, roles):
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            email=email,
            full_name=name,
            roles=roles,
            password_hash=hash_password(PASSWORD),
            email_verified=True,
            is_active=True,
        )
        db.add(user)
        db.flush()
    elif not user.is_active or not set(roles).issubset(set(user.roles)):
        raise RuntimeError(
            f"Demo account {email} has changed permissions; existing data was preserved"
        )
    return user


def seed():
    if get_settings().app_env == "production":
        raise RuntimeError("Paid-event demo seeding is disabled in production")
    create_schema()
    created = 0
    now = datetime.now(UTC)
    with SessionLocal() as db:
        organizer = ensure_user(
            db, ORGANIZER_EMAIL, "Demo Paid Event Organizer", ["attendee", "organizer"]
        )
        admin = ensure_user(db, ADMIN_EMAIL, "Demo Administrator", ["attendee", "platform_admin"])
        profile = db.scalar(
            select(OrganizerProfile).where(OrganizerProfile.user_id == organizer.id)
        )
        if profile is None:
            db.add(
                OrganizerProfile(
                    user_id=organizer.id,
                    contact_name="Demo Paid Event Organizer",
                    organization_name="Demo Events KZ",
                    contact_phone="+77000000000",
                    verification_status="verified",
                    payout_verified=True,
                    payout_account_masked="DEMO ****1234",
                )
            )
        db.commit()
        for index, (slug, title, category, city, target, price, capacity) in enumerate(SAMPLES):
            event_id = uuid5(NAMESPACE_URL, f"biletflow:paid-review-demo:v1:{slug}")
            if db.get(Event, event_id) is not None:
                continue  # Never reset user decisions, edits, payments or publication.
            starts = now + timedelta(days=28 + index * 3)
            venue = Venue(name=f"Demo {city} Event Hall", address="1 Demo Street", city=city)
            db.add(venue)
            db.flush()
            event = Event(
                id=event_id,
                organizer_id=organizer.id,
                venue_id=venue.id,
                title=title,
                description=(
                    "Sample event for exploring manual paid-event review. "
                    "Organizer contact, venue and payment details are demonstration fixtures only. "
                    "General admission includes entry to the full programme."
                ),
                category=category,
                visibility="public",
                status="draft",
                starts_at=starts,
                ends_at=starts + timedelta(hours=3),
                time_zone="Asia/Almaty",
                registration_opens_at=now - timedelta(days=1),
                registration_closes_at=starts,
                capacity=capacity,
                refund_policy=("Full demonstration refunds may be requested "
                               "before the event starts."),
                activation_fee_paid=True,
                paid_terms_accepted_at=now,
                paid_sales_status="not_ready",
            )
            db.add(event)
            db.flush()
            db.add(
                TicketType(
                    event_id=event.id,
                    name="General admission",
                    kind="paid",
                    description="Standard event admission",
                    price_minor=price,
                    quantity=capacity,
                    sales_start=now - timedelta(days=1),
                    sales_end=starts,
                    max_per_order=5,
                )
            )
            db.flush()
            submit(db, event.id, organizer)
            if target == "rejected":
                decide(
                    event.id,
                    "reject",
                    ReviewDecision(
                        reason=(
                            "Please explain the venue accessibility arrangements and "
                        "add the organizer's event-day contact to the description."
                        )
                    ),
                    admin,
                    db,
                )
            elif target in ("approved", "changes_required"):
                decide(
                    event.id,
                    "approve",
                    ReviewDecision(reason="Demo review: details and simulated fee checked."),
                    admin,
                    db,
                )
                if target == "changes_required":
                    invalidate_review(db, event, organizer)
                    event.description += (
                        " The workshop programme was updated after the demo approval."
                    )
                    db.commit()
            audit(
                db,
                admin,
                event,
                "demo.paid_review_seeded",
                "event",
                event.id,
                f"Sample paid-event application created with {target} fixture state",
            )
            db.commit()
            created += 1
    print(f"Paid-event samples ready: {created} added, {len(SAMPLES) - created} preserved.")
    print(f"Admin: {ADMIN_EMAIL}; organizer: {ORGANIZER_EMAIL}")
    print("New demo accounts use DemoPass123!; existing passwords are unchanged.")


if __name__ == "__main__":
    seed()
