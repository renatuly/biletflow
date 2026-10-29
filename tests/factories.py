from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.users.models import User

PASSWORD = "test-password-123"
API = "/api/v1"


def account(client, engine, email, role=None, verify=True):
    response = client.post(
        f"{API}/auth/register",
        json={"email": email, "password": PASSWORD, "full_name": "Test User"},
    )
    assert response.status_code == 201, response.text
    if verify:
        token = response.headers["X-Demo-Verification-Token"]
        assert client.post(f"{API}/auth/verify-email", json={"token": token}).status_code == 200
    if role:
        with Session(engine) as db:
            user = db.scalar(select(User).where(User.email == email))
            user.roles = [role]
            db.commit()
    return login(client, email)


def login(client, email, password=PASSWORD):
    response = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def event(client, organizer, title="Test event"):
    now = datetime.now(UTC)
    start = now + timedelta(days=3)
    response = client.post(
        f"{API}/events",
        headers=organizer,
        json={
            "title": title,
            "description": "Integration test event",
            "category": "music",
            "venue": {"name": "Hall", "address": "1 Abay Ave", "city": "Almaty"},
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(hours=2)).isoformat(),
            "registration_opens_at": (now - timedelta(hours=1)).isoformat(),
            "registration_closes_at": start.isoformat(),
            "capacity": 100,
            "refund_policy": "Full refund until 48 hours before the event.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def ticket_type(client, organizer, event_id, name="General", price_minor=0, quantity=20):
    now = datetime.now(UTC)
    response = client.post(
        f"{API}/events/{event_id}/ticket-types",
        headers=organizer,
        json={
            "name": name,
            "kind": "paid" if price_minor else "free",
            "price_minor": price_minor,
            "quantity": quantity,
            "sales_start": (now - timedelta(hours=1)).isoformat(),
            "sales_end": (now + timedelta(days=2)).isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def activate_paid_sales(client, engine, organizer, event_id):
    assert client.put(
        f"{API}/organizer-profile", headers=organizer, json={"contact_name": "Organizer"}
    ).is_success
    assert client.post(f"{API}/organizer-profile/verification/submit", headers=organizer).is_success
    assert client.put(
        f"{API}/organizer-profile/payout-account",
        headers=organizer,
        params={"account_reference": "KZ000000001234"},
    ).is_success
    for step in ("accept-terms", "activation-fee", "apply"):
        response = client.post(f"{API}/events/{event_id}/paid-sales/{step}", headers=organizer)
        assert response.is_success, response.text
    # Paid events go on sale only after a platform administrator approves them.
    admin = account(client, engine, f"admin-{event_id}@example.com", "platform_admin")
    approved = client.post(
        f"{API}/admin/paid-event-applications/{event_id}/approve", headers=admin, json={}
    )
    assert approved.is_success, approved.text
    activated = client.post(f"{API}/events/{event_id}/paid-sales/activate", headers=organizer)
    assert activated.is_success, activated.text


def publish(client, organizer, event_id):
    response = client.post(f"{API}/events/{event_id}/publish", headers=organizer)
    assert response.status_code == 200, response.text


def checkout(client, attendee, event_id, items, promo_code=None):
    return client.post(
        f"{API}/checkout/sessions",
        headers=attendee,
        json={
            "event_id": event_id,
            "attendee_name": "Aida Attendee",
            "attendee_email": "aida@example.com",
            "items": [{"ticket_type_id": t, "quantity": q} for t, q in items],
            "promo_code": promo_code,
        },
    )


def complete(client, attendee, order_id, outcome="success"):
    return client.post(
        f"{API}/checkout/sessions/{order_id}/complete",
        headers=attendee,
        json={"outcome": outcome},
    )


def campaign(client, organizer, event_id, **overrides):
    now = datetime.now(UTC)
    body = {
        "name": "Campus launch",
        "code": "campus20",
        "discount_type": "percentage",
        "discount_value": 20,
        "valid_from": (now - timedelta(hours=1)).isoformat(),
        "valid_until": (now + timedelta(days=1)).isoformat(),
        "max_redemptions": 10,
        **overrides,
    }
    return client.post(f"{API}/events/{event_id}/campaigns", headers=organizer, json=body)
