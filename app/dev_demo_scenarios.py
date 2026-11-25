"""Create demo-session scenarios by driving the real API, so every record is produced by
the same business logic the apps use (refunds, redemptions, check-ins, audit timeline).

Run after the base seeds:
    python -m app.dev_seed
    python -m app.dev_paid_event_seed
    python -m app.dev_support_seed
    python -m app.dev_demo_scenarios

Idempotent: if the marker campaign already exists, nothing is created and only the cheat
sheet is printed. All payments are simulated; no real money or email is involved.
"""

import os

# Keep this script offline: capture emails in memory regardless of the local .env.
os.environ["EMAIL_BACKEND"] = "memory"

import random  # noqa: E402
import uuid  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.dev_seed import DEMO_PASSWORD, DEMO_PROMO_CODE  # noqa: E402
from app.events.models import Event  # noqa: E402
from app.main import app  # noqa: E402
from app.orders.models import Order, Payment  # noqa: E402
from app.tickets.models import Ticket  # noqa: E402
from app.users.models import User  # noqa: E402

MARKER_CODE = "EARLYBIRD1"
SHOWCASE_EVENT = "Almaty Jazz Night"  # paid, published, owned by organizer.demo, staff assigned
FREE_EVENT = "Startup Pitch Night"  # free, published, owned by organizer.demo
REPORTED_EVENT = "Astana Football Derby"  # owned by city.organizer
SUSPENDED_EVENT = "Kazakh Design Expo"  # owned by city.organizer
EXTRA_USERS = (
    ("applicant.demo@example.com", "Demo Organizer Applicant", ["attendee"]),
    ("suspended.demo@example.com", "Demo Suspended User", ["attendee"]),
)

API = get_settings().app_api_v1_prefix
client = TestClient(app)
_tokens: dict[str, str] = {}


def call(method: str, path: str, who: str | None = None, expect=(200, 201, 202, 204), **kw):
    headers = kw.pop("headers", {})
    if who:
        headers["Authorization"] = f"Bearer {token(who)}"
    response = client.request(method, f"{API}{path}", headers=headers, **kw)
    if response.status_code not in expect:
        raise RuntimeError(f"{method} {path} as {who}: {response.status_code} {response.text}")
    return response.json() if response.content else None


def token(email: str) -> str:
    if email not in _tokens:
        _tokens[email] = call(
            "POST", "/auth/login", json={"email": email, "password": DEMO_PASSWORD}
        )["access_token"]
    return _tokens[email]


def event_id(title: str) -> str:
    with SessionLocal() as db:
        return str(db.scalar(select(Event.id).where(Event.title == title)))


def ticket_types(eid: str) -> dict[str, str]:
    return {t["name"]: t["id"] for t in call("GET", f"/events/{eid}/ticket-types")}


def buy(who: str, eid: str, items: list[tuple[str, int]], promo=None, outcome="success"):
    name = call("GET", "/users/me", who)["full_name"]
    order = call(
        "POST",
        "/checkout/sessions",
        who,
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={
            "event_id": eid,
            "attendee_name": name,
            "attendee_email": who,
            "items": [{"ticket_type_id": t, "quantity": q} for t, q in items],
            "promo_code": promo,
        },
    )
    if outcome == "abandon":
        call("POST", f"/checkout/sessions/{order['id']}/cancel", who)
    else:
        call(
            "POST",
            f"/checkout/sessions/{order['id']}/complete",
            who,
            expect=(200, 201, 202, 402, 409),
            json={"outcome": outcome},
        )
    return order["id"]


def order_tickets(order_id: str) -> list[Ticket]:
    from app.orders.models import OrderItem

    with SessionLocal() as db:
        return list(
            db.scalars(
                select(Ticket)
                .join(OrderItem, Ticket.order_item_id == OrderItem.id)
                .where(OrderItem.order_id == uuid.UUID(order_id))
            )
        )


def ensure_extra_users() -> None:
    with SessionLocal() as db:
        for email, name, roles in EXTRA_USERS:
            user = db.scalar(select(User).where(User.email == email))
            if user is None:
                db.add(
                    User(
                        email=email,
                        full_name=name,
                        roles=roles,
                        password_hash=hash_password(DEMO_PASSWORD),
                        email_verified=True,
                        is_active=True,
                    )
                )
        db.commit()


def approve_seeded_paid_events() -> int:
    """The base seed marks published paid events 'active' but creates no admin review, and
    checkout now requires an approved review matching the event's current fingerprint."""
    from app.events.paid_applications import PaidEventApplication, application, fingerprint
    from app.tickets.models import TicketType

    now = datetime.now(UTC)
    approved = 0
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == "admin.demo@example.com"))
        events = db.scalars(
            select(Event)
            .join(TicketType, TicketType.event_id == Event.id)
            .where(Event.status == "published", TicketType.kind == "paid")
            .distinct()
        ).all()
        for event in events:
            if application(db, event) is not None:
                continue
            db.add(
                PaidEventApplication(
                    event_id=event.id,
                    status="approved",
                    snapshot_hash=fingerprint(db, event),
                    submitted_at=now - timedelta(days=21),
                    reviewed_at=now - timedelta(days=20),
                    reviewed_by=admin.id,
                    reason="Demo review: details and simulated fee checked.",
                )
            )
            approved += 1
        db.commit()
    return approved


def backdate_orders() -> int:
    """Spread confirmed orders over the last 3 weeks so 'sales over time' has a real curve."""
    rng = random.Random(361)
    now = datetime.now(UTC)
    with SessionLocal() as db:
        orders = db.scalars(select(Order).where(Order.status != "pending")).all()
        for order in orders:
            when = now - timedelta(days=rng.randint(0, 20), hours=rng.randint(0, 23))
            order.created_at = when
            for payment in db.scalars(select(Payment).where(Payment.order_id == order.id)):
                payment.created_at = when
        db.commit()
        return len(orders)


def build() -> dict:
    org, staff, admin = (
        "organizer.demo@example.com",
        "staff.demo@example.com",
        "admin.demo@example.com",
    )
    me = "attendee.demo@example.com"
    guests = [f"guest{i:02d}.demo@example.com" for i in range(1, 21)]
    jazz, free = event_id(SHOWCASE_EVENT), event_id(FREE_EVENT)
    jazz_types = ticket_types(jazz)
    general, vip = jazz_types["General admission"], jazz_types["VIP"]
    out: dict = {}

    # 1. Campaigns: the seeded 20% code gets real redemptions; a 1-use fixed code gets exhausted.
    now = datetime.now(UTC)
    existing_codes = {c["code"] for c in call("GET", f"/events/{jazz}/campaigns", org)}
    if MARKER_CODE not in existing_codes:
        call(
            "POST",
            f"/events/{jazz}/campaigns",
            org,
            json={
                "name": "Early bird (1 use)",
                "code": MARKER_CODE,
                "discount_type": "fixed_kzt",
                "discount_value": 200_000,
                "valid_from": (now - timedelta(days=1)).isoformat(),
                "valid_until": (now + timedelta(days=6)).isoformat(),
                "max_redemptions": 1,
            },
        )
    for g in guests[12:16]:
        buy(g, jazz, [(general, 1)], promo=DEMO_PROMO_CODE)
    buy(guests[16], jazz, [(vip, 1)], promo=MARKER_CODE)  # uses the only redemption

    # 2. Ordinary paid sales incl. VIP and a multi-ticket order.
    buy(guests[17], jazz, [(vip, 2)])
    buy(guests[18], jazz, [(general, 3)])

    # 3. Payment failure and an abandoned checkout: neither may produce tickets.
    out["failed_order"] = buy(guests[19], jazz, [(vip, 1)], outcome="failure")
    out["abandoned_order"] = buy(guests[19], jazz, [(general, 1)], outcome="abandon")

    # 4. The demo attendee's tickets in every scanner state (all for the showcase event).
    valid_order = buy(me, jazz, [(general, 1)])
    used_order = buy(me, jazz, [(general, 1)])
    refunded_order = buy(me, jazz, [(vip, 1)])
    undo_order = buy(me, jazz, [(general, 1)])
    used, undo = order_tickets(used_order)[0], order_tickets(undo_order)[0]
    call("POST", f"/check-in/events/{jazz}/admit", staff, params={"qr_payload": used.qr_credential})
    record = call(
        "POST", f"/check-in/events/{jazz}/admit", staff, params={"qr_payload": undo.qr_credential}
    )
    call("POST", f"/check-in/records/{record['record_id']}/reverse", staff)
    call(
        "POST",
        f"/orders/{refunded_order}/refunds",
        org,
        json={"reason": "[Demo] Attendee can no longer attend"},
    )
    # Free registration cancelled by the organizer -> cancelled ticket.
    cancelled_order = buy(me, free, [(ticket_types(free)["General admission"], 1)])
    call(
        "POST",
        f"/orders/{cancelled_order}/cancel-registration",
        org,
        json={"reason": "[Demo] Duplicate registration"},
    )
    # A few more check-ins so attendance analytics are not zero.
    with SessionLocal() as db:
        from app.orders.models import OrderItem

        guest_tickets = db.scalars(
            select(Ticket)
            .join(OrderItem, Ticket.order_item_id == OrderItem.id)
            .join(Order, OrderItem.order_id == Order.id)
            .where(Ticket.event_id == uuid.UUID(jazz), Ticket.status == "valid")
            .where(Order.attendee_email.in_(guests[:6]))
        ).all()
        qr = [t.qr_credential for t in guest_tickets]
    for payload in qr:
        call("POST", f"/check-in/events/{jazz}/admit", staff, params={"qr_payload": payload})
    out["tickets"] = {
        "valid": str(order_tickets(valid_order)[0].id),
        "already_used": str(used.id),
        "check_in_reversed (valid again)": str(undo.id),
        "refunded": str(order_tickets(refunded_order)[0].id),
        "cancelled (free event)": str(order_tickets(cancelled_order)[0].id),
    }

    # 5. Event update -> attendee notification + timeline entry. Done on the FREE event:
    # editing an approved paid event invalidates its review and blocks checkout again.
    call(
        "PATCH",
        f"/events/{free}",
        org,
        json={
            "description": "Early-stage teams pitch new products to founders, mentors, and "
            "investors. Update: doors open 30 minutes earlier."
        },
    )

    # 6. Disputes and reports for the admin moderation queues.
    guest_order = call("GET", "/orders", guests[17])
    guest_order = guest_order["items"] if isinstance(guest_order, dict) else guest_order
    call(
        "POST",
        f"/orders/{guest_order[0]['id']}/disputes",
        guests[17],
        json={"reason": "[Demo] I was charged for two VIP tickets but expected one."},
    )
    call(
        "POST",
        f"/events/{event_id(REPORTED_EVENT)}/reports",
        guests[2],
        json={"reason": "[Demo] Event description promises prizes that seem misleading."},
    )

    # 7. Organizer onboarding: a pending application waiting for admin approval.
    call(
        "POST",
        "/organizer-applications",
        "applicant.demo@example.com",
        json={
            "contact_name": "Demo Organizer Applicant",
            "organization_name": "NU Robotics Club",
            "contact_phone": "+7 700 000 0000",
            "description": "[Demo] Student club planning free workshops and a paid hackathon.",
        },
    )

    # 8. Admin moderation: a suspended user and an event with paid sales suspended.
    users = call("GET", "/admin/users", admin, params={"q": "suspended.demo"})
    users = users["items"] if isinstance(users, dict) else users
    call(
        "POST",
        f"/admin/users/{users[0]['id']}/suspend",
        admin,
        json={"reason": "[Demo] Repeated chargeback attempts"},
    )
    call("POST", f"/admin/events/{event_id(SUSPENDED_EVENT)}/suspend", admin)
    return out


def cheat_sheet(out: dict | None) -> None:
    print(f"\n=== Demo scenarios (password for every account: {DEMO_PASSWORD}) ===")
    print(f"Showcase event: '{SHOWCASE_EVENT}' (organizer.demo, staff.demo assigned)")
    print("  Promo BILETFLOW20 (20%): has real redemptions -> organizer Campaigns/analytics")
    print(f"  Promo {MARKER_CODE} (fixed 2 000 KZT, 1 use): already used -> shows 'exhausted'")
    print("  Includes VIP/multi-ticket orders, 1 failed payment, 1 abandoned checkout,")
    print("  1 refund, ~8 check-ins, 1 reversed check-in, an event-update notification.")
    if out:
        print("attendee.demo@example.com tickets for scanner states (open at /tickets):")
        for state, tid in out["tickets"].items():
            print(f"  {state:32} {tid}")
    print("Admin queues (admin.demo): pending organizer application from applicant.demo,")
    print(f"  1 dispute, 1 report on '{REPORTED_EVENT}', suspended user suspended.demo,")
    print(f"  paid sales suspended on '{SUSPENDED_EVENT}', 5 paid-sales review applications.")
    print("Campaign-QR rejection: scan the purple campaign QR with the staff app -> rejected.")


def main() -> None:
    if get_settings().app_env == "production":
        raise RuntimeError("Demo scenarios are disabled when APP_ENV=production")
    # Marker: the organizer application is one of the last records build() creates.
    from app.users.models import OrganizerApplication

    with SessionLocal() as db:
        done = db.scalar(
            select(OrganizerApplication.id)
            .join(User, User.id == OrganizerApplication.user_id)
            .where(User.email == "applicant.demo@example.com")
        )
    if done:
        print("Demo scenarios already exist; nothing created.")
        cheat_sheet(None)
        return
    ensure_extra_users()
    print(f"Recorded approved paid-sales reviews for {approve_seeded_paid_events()} events.")
    out = build()
    count = backdate_orders()
    print(f"Spread {count} completed orders across the last 21 days for the sales chart.")
    cheat_sheet(out)


if __name__ == "__main__":
    main()
