"""Build a fresh, verified local demo; --replace-local backs up and replaces SQLite data.

Run from the backend: .venv/bin/python -m app.dev_reset_seed --replace-local
All outgoing email is captured in memory. Payments/payouts are simulations.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

PASSWORD = "DemoPass123!"
ACCOUNTS = {
    "attendee": ("attendee.demo@example.com", "Amina Sadykova", ["attendee"]),
    "organizer": ("organizer.demo@example.com", "Daniyar Nurpeis", ["attendee", "organizer"]),
    "staff": ("staff.demo@example.com", "Miras Alim", ["attendee", "event_admin"]),
    "support": ("support.demo@example.com", "Dana Serik", ["attendee", "event_admin"]),
    "admin": ("admin.demo@example.com", "Aliya Beket", ["attendee", "platform_admin"]),
    "city": (
        "city.organizer.demo@example.com",
        "Steppe Culture Collective",
        ["attendee", "organizer"],
    ),
    "new": ("new.attendee.demo@example.com", "Asel Omar", ["attendee"]),
    "applicant": ("applicant.demo@example.com", "Timur Sarsen", ["attendee"]),
    "rejected": ("rejected.applicant.demo@example.com", "Samal Tuleu", ["attendee"]),
    "unverified": ("unverified.demo@example.com", "Nursultan Ilyas", ["attendee"]),
    "suspended": ("suspended.demo@example.com", "Arman Sapar", ["attendee"]),
}
# slug, title, category, city, days ahead, price (tiyn), capacity, owner
EVENTS = [
    (
        "aurora",
        "Aurora Live: Music Under the Stars",
        "music",
        "Almaty",
        7,
        850000,
        180,
        "organizer",
    ),
    ("run", "Steppe Sunrise 10K", "sport", "Astana", 10, 450000, 120, "organizer"),
    (
        "founders",
        "Founders Table: Central Asia",
        "business",
        "Almaty",
        14,
        1200000,
        80,
        "organizer",
    ),
    ("art", "Colour Lab: Contemporary Printmaking", "art", "Shymkent", 9, 0, 60, "organizer"),
    ("food", "Nomad Kitchen: Regional Tasting Fair", "food", "Almaty", 12, 250000, 140, "city"),
    ("tech", "Build Tomorrow: AI Makers Day", "tech", "Astana", 18, 600000, 160, "organizer"),
    ("student", "Campus Connect: Clubs and Careers", "student", "Almaty", 5, 0, 200, "organizer"),
    ("comedy", "Late Mic: Stories and Stand-up", "other", "Astana", 16, 350000, 100, "city"),
    ("live", "Community Makers: Open Studio", "art", "Almaty", 2, 0, 70, "organizer"),
    ("soldout", "Clay and Coffee: Small Group Workshop", "art", "Almaty", 6, 0, 6, "organizer"),
    ("private", "Inner Circle: Creative Supper", "food", "Almaty", 20, 0, 30, "organizer"),
    (
        "unlisted",
        "Mentor Office Hours: Product Clinic",
        "business",
        "Astana",
        22,
        0,
        20,
        "organizer",
    ),
    ("cancelled", "Mountain Echo: Acoustic Escape", "music", "Almaty", 11, 500000, 60, "organizer"),
    ("suspended", "Night Market: Neon Edition", "food", "Shymkent", 15, 150000, 90, "city"),
    (
        "salespaused",
        "Arena Pulse: Basketball Showcase",
        "sport",
        "Astana",
        19,
        700000,
        100,
        "organizer",
    ),
    ("draft", "Future Frames: Short Film Evening", "other", "Almaty", 32, 0, 80, "organizer"),
    ("pending", "Cloud Native: Engineering Forum", "tech", "Astana", 28, 950000, 110, "organizer"),
    (
        "approved",
        "Steppe Sessions: Chamber Quartet",
        "music",
        "Almaty",
        35,
        550000,
        70,
        "organizer",
    ),
    ("rejected", "Taste Studio: Chef Masterclass", "food", "Almaty", 30, 800000, 40, "organizer"),
    ("changes", "Design Futures: Portfolio Review", "art", "Astana", 33, 400000, 50, "organizer"),
    (
        "pastpaid",
        "Founders Breakfast: Autumn Edition",
        "business",
        "Almaty",
        3,
        300000,
        80,
        "organizer",
    ),
    ("pastready", "Code Together: Community Hackday", "tech", "Astana", 4, 200000, 80, "organizer"),
    ("pastblocked", "City Voices: Spoken Word", "other", "Shymkent", 5, 250000, 80, "organizer"),
    (
        "setup",
        "Riverside Sounds: New Organizer Draft",
        "music",
        "Almaty",
        40,
        650000,
        100,
        "organizer",
    ),
    ("closed", "Green Campus: Volunteer Morning", "student", "Almaty", 24, 0, 60, "organizer"),
]


def build():
    # Set before importing app config; never deliver demo mail to a real mailbox.
    os.environ["EMAIL_BACKEND"] = "memory"
    from fastapi.testclient import TestClient
    from sqlalchemy import func, select

    from app import models as m
    from app.core.config import get_settings
    from app.core.database import Base, SessionLocal, create_schema, engine
    from app.core.security import hash_password
    from app.events.paid_applications import fingerprint
    from app.events.service import audit
    from app.main import app
    from app.orders.reservations import expire_event

    if get_settings().app_env != "development" or engine.dialect.name != "sqlite":
        raise RuntimeError("Demo builds require development SQLite")
    create_schema()
    with SessionLocal() as db:
        if db.scalar(select(func.count(m.User.id))) or db.scalar(select(func.count(m.Event.id))):
            raise RuntimeError("Build requires an empty staging database")
    now = datetime.now(UTC)
    users, events, types, out = {}, {}, {}, {"built_at": now.isoformat()}
    client = TestClient(app)
    tokens = {}

    def call(method, path, who=None, expected=(200, 201, 202, 204), **kw):
        headers = kw.pop("headers", {})
        if who:
            if who not in tokens:
                r = client.post(
                    "/api/v1/auth/login", json={"email": users[who].email, "password": PASSWORD}
                )
                if r.status_code != 200:
                    raise RuntimeError(f"Login {who}: {r.status_code} {r.text}")
                tokens[who] = r.json()["access_token"]
            headers["Authorization"] = "Bearer " + tokens[who]
        r = client.request(method, "/api/v1" + path, headers=headers, **kw)
        if r.status_code not in expected:
            raise RuntimeError(f"{method} {path} as {who}: {r.status_code} {r.text}")
        return r.json() if r.content and "json" in r.headers.get("content-type", "") else r

    with SessionLocal() as db:
        password_hash = hash_password(PASSWORD)
        names = [
            "Aruzhan",
            "Dias",
            "Aigerim",
            "Madi",
            "Tomiris",
            "Alikhan",
            "Madina",
            "Temirlan",
            "Aidana",
            "Sanzhar",
            "Kamila",
            "Olzhas",
            "Zarina",
            "Adil",
            "Aisha",
            "Erlan",
            "Aliya",
            "Bekzat",
            "Sabina",
            "Daniyar",
            "Nazym",
            "Rauan",
            "Anel",
            "Yerkebulan",
        ]
        records = dict(ACCOUNTS)
        records.update(
            {
                f"guest{i}": (f"guest{i:02}.fresh.demo@example.com", name + " Aman", ["attendee"])
                for i, name in enumerate(names, 1)
            }
        )
        for i, (key, (email, name, roles)) in enumerate(records.items()):
            user = m.User(
                email=email,
                full_name=name,
                roles=roles,
                password_hash=password_hash,
                email_verified=key != "unverified",
                preferred_locale=("kk", "ru", "en")[i % 3],
                phone=f"+7700000{i:04}",
            )
            db.add(user)
            db.flush()
            users[key] = user
        for key in ("organizer", "city"):
            db.add(
                m.OrganizerProfile(
                    user_id=users[key].id,
                    contact_name=users[key].full_name,
                    organization_name="Aurora Events Lab"
                    if key == "organizer"
                    else "Steppe Culture Collective",
                    contact_phone=users[key].phone,
                    verification_status="verified",
                    payout_verified=True,
                    payout_account_masked="DEMO **** 2468",
                )
            )
        db.add(
            m.PlatformSettings(
                key="platform", activation_fee_minor=100000, processing_fee_bps=300, hold_minutes=15
            )
        )
        db.commit()

    # Create fresh venues/events/types through the same endpoints as the clients.
    draft_states = {"draft", "pending", "approved", "rejected", "changes", "setup"}
    for slug, title, category, city, days, price, capacity, owner in EVENTS:
        start = (now + timedelta(days=days)).replace(hour=13, minute=0, second=0, microsecond=0)
        event = call(
            "POST",
            "/events",
            owner,
            json={
                "title": title,
                "description": f"{title} brings our community together in {city}. "
                "Join a curated programme with local hosts, accessible entry "
                "and time to meet other guests. "
                "Doors open 30 minutes before the programme. Contact the "
                "event team through your booking for assistance.",
                "category": category,
                "visibility": "private"
                if slug == "private"
                else "unlisted"
                if slug == "unlisted"
                else "public",
                "venue": {
                    "name": f"{city} "
                    + (
                        "Creative Hall"
                        if category in ("art", "music", "other")
                        else "Community Centre"
                    ),
                    "address": f"{20 + days} Abay Avenue",
                    "city": city,
                },
                "starts_at": start.isoformat(),
                "ends_at": (start + timedelta(hours=4)).isoformat(),
                "registration_opens_at": (now - timedelta(days=45)).isoformat(),
                "registration_closes_at": start.isoformat(),
                "capacity": capacity,
                "refund_policy": "Full simulated refunds available before the event starts. "
                "Contact the organizer from your booking.",
            },
        )
        eid = events[slug] = event["id"]
        ticket_specs = [
            (
                "General admission",
                price,
                (capacity - 5 if slug == "draft" else capacity) if not price else capacity - 20,
            )
        ]
        if price:
            ticket_specs += [("Backstage / VIP", price * 2, 15), ("Community guest", 0, 5)]
        types[slug] = {}
        for name, value, quantity in ticket_specs:
            t = call(
                "POST",
                f"/events/{eid}/ticket-types",
                owner,
                json={
                    "name": name,
                    "description": "Full programme entry"
                    if not value
                    else "Event admission and programme access",
                    "kind": "paid" if value else "free",
                    "price_minor": value,
                    "quantity": quantity,
                    "sales_start": (now - timedelta(days=45)).isoformat(),
                    "sales_end": start.isoformat(),
                    "max_per_order": 6,
                },
            )
            types[slug][name] = t["id"]
        if price and slug != "setup":
            call("POST", f"/events/{eid}/paid-sales/accept-terms", owner)
            call(
                "POST",
                f"/events/{eid}/paid-sales/activation-fee",
                owner,
                json={"outcome": "success"},
            )
            call("POST", f"/events/{eid}/paid-sales/apply", owner)
            if slug != "pending":
                call(
                    "POST",
                    f"/admin/paid-event-applications/{eid}/"
                    + ("reject" if slug == "rejected" else "approve"),
                    "admin",
                    json={
                        "reason": "Add allergen information and an event-day contact."
                        if slug == "rejected"
                        else "Venue, policy and simulated activation verified."
                    },
                )
            if slug == "changes":
                call(
                    "PATCH",
                    f"/events/{eid}",
                    owner,
                    json={
                        "description": event["description"] + " Updated portfolio review programme."
                    },
                )
        if slug not in draft_states:
            call("POST", f"/events/{eid}/publish", owner)
        if owner == "organizer" and slug not in draft_states:
            call("POST", f"/events/{eid}/staff", owner, params={"email": users["staff"].email})
        if slug in ("aurora", "student", "live", "run"):
            call(
                "POST",
                f"/support/events/{eid}/staff",
                owner,
                json={"email": users["support"].email},
            )

    hidden = call(
        "POST",
        f"/events/{events['draft']}/ticket-types",
        "organizer",
        json={
            "name": "Crew preview",
            "description": "Hidden invitation-only crew preview",
            "kind": "free",
            "price_minor": 0,
            "quantity": 5,
            "sales_start": (now - timedelta(days=1)).isoformat(),
            "sales_end": (now + timedelta(days=31)).isoformat(),
            "max_per_order": 1,
        },
    )
    call("POST", f"/events/{events['draft']}/ticket-types/{hidden['id']}/hide", "organizer")

    def buy(
        who, slug, quantity=1, ticket="General admission", promo=None, outcome="success", items=None
    ):
        order = call(
            "POST",
            "/checkout/sessions",
            who,
            headers={"Idempotency-Key": str(uuid4())},
            json={
                "event_id": events[slug],
                "attendee_name": users[who].full_name,
                "attendee_email": users[who].email,
                "items": items or [{"ticket_type_id": types[slug][ticket], "quantity": quantity}],
                "promo_code": promo,
            },
        )
        oid = order["id"]
        if outcome == "cancel":
            call("POST", f"/checkout/sessions/{oid}/cancel", who)
        elif outcome == "expire":
            with SessionLocal() as db:
                db.get(m.Order, UUID(oid)).expires_at = now - timedelta(minutes=1)
                db.commit()
                expire_event(db, UUID(events[slug]))
                db.commit()
        elif outcome != "pending":
            call(
                "POST",
                f"/checkout/sessions/{oid}/complete",
                who,
                expected=(200,),
                json={"outcome": outcome},
            )
        return oid

    def ticket_for(oid):
        with SessionLocal() as db:
            return db.scalar(
                select(m.Ticket)
                .join(m.OrderItem, m.Ticket.order_item_id == m.OrderItem.id)
                .where(m.OrderItem.order_id == UUID(oid))
            )

    # Event-specific campaigns: active, exhausted, disabled, expired and scheduled.
    for code, name, discount, value, limit, begin, end in [
        ("AURORA20", "Community launch: 20%", "percentage", 20, 100, -2, 6),
        ("FIRSTLIGHT", "First light: 1,500 KZT off", "fixed_kzt", 150000, 1, -2, 6),
        ("PAUSED15", "Partner offer on pause", "percentage", 15, 40, -2, 6),
        ("LASTWEEK", "Previous week offer", "percentage", 10, 30, -9, -1),
        ("NEXTWAVE", "Next wave launch", "percentage", 12, 40, 2, 6),
    ]:
        c = call(
            "POST",
            f"/events/{events['aurora']}/campaigns",
            "organizer",
            json={
                "code": code,
                "name": name,
                "discount_type": discount,
                "discount_value": value,
                "max_redemptions": limit,
                "valid_from": (now + timedelta(days=begin)).isoformat(),
                "valid_until": (now + timedelta(days=end)).isoformat(),
            },
        )
        if code == "PAUSED15":
            call(
                "PATCH",
                f"/events/{events['aurora']}/campaigns/{c['id']}",
                "organizer",
                json={"enabled": False},
            )
        if code == "AURORA20":
            out["campaign_url"] = c["campaign_url"]

    # Multiple event and ticket types, healthy inventory, and a visible sales curve.
    base_orders = {}
    for slug in (
        "aurora",
        "run",
        "founders",
        "art",
        "food",
        "tech",
        "student",
        "comedy",
        "live",
        "soldout",
        "cancelled",
        "suspended",
        "salespaused",
        "pastpaid",
        "pastready",
        "pastblocked",
    ):
        count = 6 if slug == "soldout" else 9
        for i in range(1, count + 1):
            who = "attendee" if i == 1 else f"guest{i}"
            ticket = (
                "Backstage / VIP"
                if i == 3 and "Backstage / VIP" in types[slug]
                else "General admission"
            )
            base_orders[slug, who] = buy(
                who,
                slug,
                ticket=ticket,
                promo="AURORA20" if slug == "aurora" and i in (1, 4, 5) else None,
            )
    buy("guest10", "aurora", ticket="Backstage / VIP", promo="FIRSTLIGHT")
    out["multi_ticket_order"] = buy(
        "attendee",
        "aurora",
        items=[
            {"ticket_type_id": types["aurora"]["General admission"], "quantity": 2},
            {"ticket_type_id": types["aurora"]["Backstage / VIP"], "quantity": 1},
        ],
    )
    buy("guest11", "aurora", ticket="Community guest")
    buy(
        "guest15",
        "tech",
        items=[
            {"ticket_type_id": types["tech"]["General admission"], "quantity": 1},
            {"ticket_type_id": types["tech"]["Community guest"], "quantity": 1},
        ],
    )
    out["failed_order"] = buy("attendee", "aurora", outcome="failure")
    out["abandoned_order"] = buy("attendee", "aurora", outcome="cancel")
    out["expired_order"] = buy("attendee", "aurora", outcome="expire")
    out["pending_order"] = buy("attendee", "aurora", outcome="pending")

    # Every scanner state, generated through the real admission endpoints.
    out["ticket_states"] = {}
    for label, who, slug in [
        ("valid", "attendee", "aurora"),
        ("checked_in", "attendee", "live"),
        ("reversed", "guest2", "live"),
    ]:
        t = ticket_for(base_orders[slug, who])
        out["ticket_states"][label] = {
            "ticket_id": str(t.id),
            "qr": t.qr_credential,
            "event_id": str(t.event_id),
        }
        if label != "valid":
            record = call(
                "POST",
                f"/check-in/events/{events[slug]}/admit",
                "staff",
                params={"qr_payload": t.qr_credential},
            )
            if label == "reversed":
                call("POST", f"/check-in/records/{record['record_id']}/reverse", "staff")
    for i in range(3, 7):
        t = ticket_for(base_orders["live", f"guest{i}"])
        call(
            "POST",
            f"/check-in/events/{events['live']}/admit",
            "staff",
            params={"qr_payload": t.qr_credential},
        )

    # Support requests are real conversations, with scoped staff permissions.
    def case(
        who,
        category,
        subject,
        message,
        slug=None,
        order=None,
        agent="support",
        status="open",
        replies=(),
        platform=False,
    ):
        data = {
            "flow": "organizer_platform" if platform else "attendee",
            "category": category,
            "subject": subject,
            "message": message,
        }
        if slug:
            data["event_id"] = events[slug]
        if order:
            data["order_id"] = order
        c = call("POST", "/support/cases", who, json=data)
        manager = "admin" if platform or category == "report_event" else "organizer"
        call(
            "PATCH",
            f"/support/cases/{c['id']}/assignee",
            manager,
            json={"user_id": str(users[agent].id)},
        )
        for sender, text in replies:
            call("POST", f"/support/cases/{c['id']}/messages", sender, json={"body": text})
        if status != "open":
            call("PATCH", f"/support/cases/{c['id']}/status", agent, params={"status": status})
        return c["id"]

    refund_order = buy("attendee", "aurora", ticket="Backstage / VIP")
    cid = case(
        "attendee",
        "refund",
        "Change of plans: refund request",
        "I cannot attend and would like a full refund under the policy.",
        "aurora",
        refund_order,
        agent="organizer",
        replies=[
            ("organizer", "Your request meets our policy. I will process the simulated refund now.")
        ],
    )
    call(
        "POST",
        f"/support/cases/{cid}/refund",
        "organizer",
        json={"reason": "Attendee requested a refund before the event."},
    )
    out["ticket_states"]["refunded"] = {
        "ticket_id": str(ticket_for(refund_order).id),
        "order_id": refund_order,
    }
    cancelled_order = buy("attendee", "student")
    call(
        "POST",
        f"/orders/{cancelled_order}/cancel-registration",
        "organizer",
        json={"reason": "Duplicate free registration requested by attendee."},
    )
    out["ticket_states"]["cancelled"] = {
        "ticket_id": str(ticket_for(cancelled_order).id),
        "order_id": cancelled_order,
    }
    case(
        "attendee",
        "refund",
        "Please review my running-event refund",
        "My travel dates changed. Can you review my refund eligibility?",
        "run",
        base_orders["run", "attendee"],
        agent="organizer",
    )
    case(
        "attendee",
        "ticket_delivery",
        "Help finding the ticket email",
        "My payment succeeded but I cannot find the email.",
        "aurora",
        base_orders["aurora", "attendee"],
        status="in_progress",
        replies=[
            ("support", "Your QR tickets are available in My Tickets. Have you checked spam?"),
            ("attendee", "The QR is there. Please help me confirm the email address too."),
        ],
    )
    case(
        "attendee",
        "event_information",
        "Step-free entrance and arrival time",
        "Which entrance has step-free access?",
        "aurora",
        status="waiting_for_customer",
        replies=[
            ("support", "Use the east entrance. Would you like a team member to meet you there?")
        ],
    )
    case(
        "attendee",
        "technical",
        "PDF ticket download is working",
        "Where can I download the ticket PDF?",
        "aurora",
        status="resolved",
        replies=[
            ("support", "Open the booking and choose Download ticket."),
            ("attendee", "Downloaded successfully, thank you!"),
        ],
    )
    case(
        "guest4",
        "check_in",
        "My group will arrive separately",
        "Can each person use their own QR code?",
        "live",
        status="open",
    )
    case(
        "organizer",
        "activation",
        "Paid review checklist clarification",
        "What should I update after the chef-event rejection?",
        agent="admin",
        platform=True,
        status="in_progress",
        replies=[
            ("admin", "Please add allergen information and an event-day contact, then resubmit.")
        ],
    )
    case(
        "staff",
        "technical",
        "Camera permission on a new phone",
        "The scanner needs camera permission after switching phones.",
        agent="admin",
        platform=True,
        status="waiting_for_customer",
        replies=[
            (
                "admin",
                "Enable camera access in phone settings. Which operating system are you using?",
            )
        ],
    )
    case(
        "attendee",
        "report_event",
        "Concern about market accessibility",
        "The venue description does not explain accessible entry.",
        "suspended",
        agent="admin",
    )

    # Private-event invite: an accepted booking, a fresh code to demonstrate acceptance,
    # a revoked invite and an expired invite. Cleartext code is only in the local guide.
    for who in ("guest12", "attendee", "guest13", "guest14"):
        inv = call(
            "POST",
            f"/events/{events['private']}/invitations",
            "organizer",
            json={"email": users[who].email, "days": 30},
        )
        if who == "guest12":
            call("POST", "/invitations/accept", who, json={"token": inv["token"]})
            buy(who, "private")
        elif who == "attendee":
            out["private_invitation_code"] = inv["token"]
        elif who == "guest13":
            call("POST", f"/events/{events['private']}/invitations/{inv['id']}/revoke", "organizer")
        else:
            with SessionLocal() as db:
                db.get(m.EventInvitation, UUID(inv["id"])).expires_at = now - timedelta(days=1)
                db.commit()

    # Organizer application states, plus a fresh attendee for the registration journey.
    for who in ("applicant", "rejected"):
        application = call(
            "POST",
            "/organizer-applications",
            who,
            json={
                "contact_name": users[who].full_name,
                "organization_name": "Campus Robotics Society"
                if who == "applicant"
                else "Local Makers Circle",
                "contact_phone": users[who].phone,
                "description": "We plan community workshops, student meetups and small "
                "ticketed showcases.",
            },
        )
        if who == "rejected":
            call(
                "POST",
                f"/admin/organizer-applications/{application['id']}/reject",
                "admin",
                json={
                    "reason": "Please provide a complete organization contact and "
                    "programme description."
                },
            )
    # Approved onboarding history for the two existing organizers.
    with SessionLocal() as db:
        for who in ("organizer", "city"):
            db.add(
                m.OrganizerApplication(
                    user_id=users[who].id,
                    contact_name=users[who].full_name,
                    organization_name="Aurora Events Lab"
                    if who == "organizer"
                    else "Steppe Culture Collective",
                    contact_phone=users[who].phone,
                    description="Community events and curated cultural programmes.",
                    status="approved",
                )
            )
        db.commit()

    # Formal moderation histories are separate from support conversations.
    for slug, status in (
        ("suspended", "open"),
        ("food", "under_review"),
        ("comedy", "resolved"),
        ("run", "dismissed"),
    ):
        report = call(
            "POST",
            f"/events/{events[slug]}/reports",
            "attendee",
            json={"reason": "Please verify the published venue and accessibility information."},
        )
        if status != "open":
            call(
                "PATCH",
                f"/admin/event-reports/{report['id']}",
                "admin",
                json={
                    "status": status,
                    "resolution": "Venue information reviewed; clarification requested or "
                    "supplied by the organizer.",
                },
            )
    for slug, status in (
        ("pastblocked", "open"),
        ("aurora", "under_review"),
        ("founders", "resolved"),
        ("tech", "dismissed"),
    ):
        dispute = call(
            "POST",
            f"/orders/{base_orders[slug, 'attendee']}/disputes",
            "attendee",
            json={
                "reason": "Please help reconcile this simulated payment with my booking receipt."
            },
        )
        if status != "open":
            call(
                "PATCH",
                f"/admin/disputes/{dispute['id']}",
                "admin",
                json={
                    "status": status,
                    "resolution": "Booking and simulated payment references reviewed with the "
                    "requester.",
                },
            )
    call(
        "PATCH",
        f"/events/{events['student']}",
        "organizer",
        json={
            "description": "Campus clubs, career mentors and practical sessions. "
            "Update: registration desk is at the east entrance; please arrive 20 minutes early."
        },
    )
    call("POST", f"/events/{events['cancelled']}/cancel", "organizer")
    for slug, action in (("suspended", "suspend_event"), ("salespaused", "suspend_sales")):
        call(
            "POST",
            f"/admin/events/{events[slug]}/controls",
            "admin",
            json={
                "action": action,
                "reason": "Awaiting updated venue documentation from the organizer.",
            },
        )
    call(
        "POST",
        f"/admin/users/{users['suspended'].id}/suspend",
        "admin",
        json={"reason": "Repeated misuse of demo bookings; account awaiting review."},
    )

    # Historical fixtures use coherent event/order/check-in times; moving event times
    # requires a fresh review fingerprint, just as their original historical approval.
    with SessionLocal() as db:
        for index, order in enumerate(
            db.scalars(select(m.Order).where(m.Order.status != "pending")).all()
        ):
            order.created_at = now - timedelta(days=1 + index % 21, hours=index % 12)
            for payment in db.scalars(select(m.Payment).where(m.Payment.order_id == order.id)):
                payment.created_at = order.created_at
        for slug, offset in (
            ("live", 0),
            ("pastpaid", -10),
            ("pastready", -5),
            ("pastblocked", -3),
        ):
            e = db.get(m.Event, UUID(events[slug]))
            e.starts_at = now - timedelta(hours=1) if not offset else now + timedelta(days=offset)
            e.ends_at = e.starts_at + timedelta(hours=4)
            e.registration_closes_at = e.starts_at
            e.status = "published" if not offset else "completed"
            for t in db.scalars(select(m.TicketType).where(m.TicketType.event_id == e.id)):
                t.sales_end = e.starts_at
            db.flush()
            review = db.scalar(
                select(m.PaidEventApplication).where(m.PaidEventApplication.event_id == e.id)
            )
            if review:
                review.snapshot_hash = fingerprint(db, e)
                review.submitted_at = e.starts_at - timedelta(days=40)
                review.reviewed_at = e.starts_at - timedelta(days=39)
            if offset:
                for order in db.scalars(select(m.Order).where(m.Order.event_id == e.id)):
                    order.created_at = e.starts_at - timedelta(
                        days=2 + int(order.id.hex[-1], 16) % 20
                    )
                    for payment in db.scalars(
                        select(m.Payment).where(m.Payment.order_id == order.id)
                    ):
                        payment.created_at = order.created_at
                for t in db.scalars(
                    select(m.Ticket).where(m.Ticket.event_id == e.id, m.Ticket.status == "valid")
                ):
                    t.status = "checked_in"
                    db.add(
                        m.CheckInRecord(
                            ticket_id=t.id,
                            event_id=e.id,
                            staff_user_id=users["staff"].id,
                            device_id="historical-demo",
                            created_at=e.starts_at + timedelta(minutes=15),
                        )
                    )
            audit(
                db,
                users["organizer"],
                e,
                "demo.historical_fixture",
                "event",
                e.id,
                "Historical or live demo timing fixture.",
            )
        e = db.get(m.Event, UUID(events["closed"]))
        e.registration_opens_at = now + timedelta(days=2)
        for t in db.scalars(select(m.TicketType).where(m.TicketType.event_id == e.id)):
            t.sales_start = e.registration_opens_at
        db.commit()
    call(
        "PATCH",
        f"/admin/payouts/{events['pastpaid']}",
        "admin",
        json={"status": "processing", "note": "Historical demo settlement checked."},
    )
    call(
        "PATCH",
        f"/admin/payouts/{events['pastpaid']}",
        "admin",
        json={"status": "paid", "note": "Simulated transfer recorded; no real money moved."},
    )
    call(
        "PATCH",
        f"/admin/payouts/{events['pastready']}",
        "admin",
        json={"status": "processing", "note": "Ready for the admin to record simulated payment."},
    )
    call(
        "PATCH",
        f"/admin/payouts/{events['pastblocked']}",
        "admin",
        json={
            "status": "blocked",
            "note": "Open payment dispute must be reviewed before settlement.",
        },
    )
    # New user has no orders. Unverified user remains available for email verification.
    with SessionLocal() as db:
        for who in ("attendee", "organizer", "staff", "support", "admin"):
            n = db.scalar(
                select(m.Notification)
                .where(m.Notification.user_id == users[who].id)
                .order_by(m.Notification.created_at)
            )
            if n:
                n.is_read = True
        db.commit()

    # Smoke-test real role endpoints and all database references/counters before swap.
    for who, path in [
        ("attendee", "/orders"),
        ("attendee", "/tickets"),
        ("organizer", "/organizer/events"),
        ("organizer", f"/events/{events['aurora']}/analytics"),
        ("staff", "/check-in/events"),
        ("support", "/support/cases"),
        ("admin", "/admin/paid-event-applications"),
        ("admin", "/admin/event-reports"),
        ("admin", "/admin/disputes"),
        ("organizer", "/organizer/payouts"),
    ]:
        call("GET", path, who)
    with SessionLocal() as db:
        assert set(db.scalars(select(m.Ticket.status))) == {
            "valid",
            "checked_in",
            "refunded",
            "cancelled",
        }
        assert set(db.scalars(select(m.Order.status))) == {
            "confirmed",
            "refunded",
            "cancelled",
            "payment_failed",
            "expired",
            "pending",
        }
        for t in db.scalars(select(m.TicketType)):
            sold = db.scalar(
                select(func.coalesce(func.sum(m.OrderItem.quantity), 0))
                .join(m.Order, m.OrderItem.order_id == m.Order.id)
                .where(m.OrderItem.ticket_type_id == t.id, m.Order.status == "confirmed")
            )
            held = db.scalar(
                select(func.coalesce(func.sum(m.OrderItem.quantity), 0))
                .join(m.Order, m.OrderItem.order_id == m.Order.id)
                .where(m.OrderItem.ticket_type_id == t.id, m.Order.status == "pending")
            )
            assert t.sold_count == sold, (t.name, t.sold_count, sold)
            assert t.reserved_count == held
            assert t.sold_count + t.reserved_count <= t.quantity
        out["counts"] = {
            table.name: db.scalar(select(func.count()).select_from(table))
            for table in Base.metadata.sorted_tables
        }
    with engine.connect() as connection:
        assert not list(connection.exec_driver_sql("PRAGMA foreign_key_check"))
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
    out["events"] = events
    out["accounts"] = {key: email for key, (email, _, _) in ACCOUNTS.items()}
    out["unlisted_event_id"] = events["unlisted"]
    engine.dispose()
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--replace-local",
        action="store_true",
        help="Back up and replace the configured development SQLite database",
    )
    parser.add_argument("--build", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.build:
        result = build()
        Path(args.build).write_text(json.dumps(result, indent=2))
        print(json.dumps(result["counts"], indent=2))
        return
    if not args.replace_local:
        parser.error("Use --replace-local to explicitly replace local demo data")
    from sqlalchemy.engine import make_url

    from app.core.config import get_settings

    settings = get_settings()
    url = make_url(settings.app_database_url)
    if (
        settings.app_env != "development"
        or url.get_backend_name() != "sqlite"
        or not url.database
        or url.database == ":memory:"
    ):
        raise SystemExit(
            "Reset is restricted to a development SQLite file; "
            "hosted/production databases are never reset by this command."
        )
    target = Path(url.database).resolve()
    if not target.is_file():
        raise SystemExit(f"Expected an existing local database at {target}")
    with tempfile.TemporaryDirectory(prefix="biletflow-fresh-demo-") as tmp:
        stage = Path(tmp) / "fresh.db"
        report = Path(tmp) / "report.json"
        env = dict(
            os.environ,
            APP_ENV="development",
            APP_DATABASE_URL=f"sqlite:///{stage}",
            EMAIL_BACKEND="memory",
            PYTHONPATH=os.pathsep.join(
                filter(
                    None,
                    [
                        str(Path(sys.modules["app"].__file__).resolve().parent.parent),
                        os.environ.get("PYTHONPATH", ""),
                    ],
                )
            ),
        )
        subprocess.run(
            [sys.executable, "-m", "app.dev_reset_seed", "--build", str(report)],
            env=env,
            check=True,
        )
        result = json.loads(report.read_text())
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = target.with_name(f"{target.name}.before-demo-{stamp}.bak")
        with sqlite3.connect(target) as current, sqlite3.connect(backup) as saved:
            current.backup(saved)
        # SQLite backup API commits the replacement transaction as a unit. Never
        # unlink an open database file: running API processes keep the same inode.
        with sqlite3.connect(stage) as fresh, sqlite3.connect(target, timeout=30) as current:
            fresh.backup(current)
        result["backup"] = str(backup)
        guide = target.parent / "demo_seed_manifest.json"
        guide.write_text(json.dumps(result, indent=2))
        print(
            f"Fresh demo installed: {target}\nPrevious data backed up: {backup}\n"
            f"Scenario IDs / invitation code: {guide}"
        )
        print(f"Demo password: {PASSWORD}")
        for key, email in result["accounts"].items():
            print(f"  {key}: {email}")
        print(
            "Sign out and sign in again in every client. Restart the API if it is already running."
        )


if __name__ == "__main__":
    main()
