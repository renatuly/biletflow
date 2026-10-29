from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.orders.models import Order
from tests.factories import (
    API,
    account,
    activate_paid_sales,
    campaign,
    checkout,
    complete,
    event,
    publish,
    ticket_type,
)


def paid_event(client, engine):
    organizer = account(client, engine, "org@example.com", "organizer")
    attendee = account(client, engine, "aida@example.com")
    event_id = event(client, organizer)
    standard = ticket_type(client, organizer, event_id, "Standard", price_minor=1_000_000)
    vip = ticket_type(client, organizer, event_id, "VIP", price_minor=3_000_000)
    free = ticket_type(client, organizer, event_id, "Free entry")
    activate_paid_sales(client, organizer, event_id)
    publish(client, organizer, event_id)
    return organizer, attendee, event_id, standard, vip, free


def quote(client, attendee, event_id, items, code):
    return client.post(
        f"{API}/promo-codes/validate",
        headers=attendee,
        json={
            "event_id": event_id,
            "items": [{"ticket_type_id": t, "quantity": q} for t, q in items],
            "promo_code": code,
        },
    )


def test_campaign_creation_rules(client, engine):
    organizer, _, event_id, standard, *_ = paid_event(client, engine)
    created = campaign(client, organizer, event_id, applicable_ticket_type_ids=[standard])
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["code"] == "CAMPUS20" and body["state"] == "active"
    assert body["campaign_url"].endswith(f"/campaign/{body['opaque_token']}")
    assert campaign(client, organizer, event_id, code="Campus20").status_code == 409
    assert campaign(client, organizer, event_id, code="X1", discount_value=10).status_code == 422
    assert campaign(client, organizer, event_id, code="BIG", discount_value=101).status_code == 422
    other = event(client, organizer, "Other event")
    foreign = ticket_type(client, organizer, other, "Elsewhere")
    assert (
        campaign(
            client, organizer, event_id, code="WRONG", applicable_ticket_type_ids=[foreign]
        ).status_code
        == 422
    )


def test_quote_discounts_and_clear_rejections(client, engine):
    organizer, attendee, event_id, standard, vip, free = paid_event(client, engine)
    campaign(client, organizer, event_id, applicable_ticket_type_ids=[vip])
    campaign(
        client,
        organizer,
        event_id,
        code="MINUS5K",
        discount_type="fixed_kzt",
        discount_value=500_000,
    )

    priced = quote(client, attendee, event_id, [(standard, 1), (vip, 1)], "campus20").json()
    assert priced["subtotal_minor"] == 4_000_000
    assert priced["discount_minor"] == 600_000  # 20% of VIP only
    assert priced["processing_fee_minor"] == 3_400_000 * 300 // 10_000
    assert priced["total_minor"] == 3_400_000 + priced["processing_fee_minor"]
    fixed = quote(client, attendee, event_id, [(standard, 2)], "MINUS5K").json()
    assert fixed["discount_minor"] == 500_000

    not_applicable = quote(client, attendee, event_id, [(standard, 1)], "CAMPUS20")
    assert not_applicable.status_code == 422
    assert "does not apply" in not_applicable.json()["detail"]
    assert (
        "does not apply"
        in quote(client, attendee, event_id, [(free, 1)], "MINUS5K").json()["detail"]
    )
    unknown = quote(client, attendee, event_id, [(vip, 1)], "NOPE")
    assert unknown.status_code == 422 and "not valid" in unknown.json()["detail"]

    order = checkout(client, attendee, event_id, [(standard, 1), (vip, 1)], "campus20").json()
    assert order["discount_minor"] == 600_000 and order["promo_code"] == "CAMPUS20"
    by_type = {item["ticket_type_id"]: item["discount_minor"] for item in order["items"]}
    assert by_type == {standard: 0, vip: 600_000}


def test_redemption_limit_holds_and_release(client, engine):
    organizer, attendee, event_id, standard, *_ = paid_event(client, engine)
    created = campaign(client, organizer, event_id, max_redemptions=1).json()

    first = checkout(client, attendee, event_id, [(standard, 1)], "CAMPUS20").json()
    held = quote(client, attendee, event_id, [(standard, 1)], "CAMPUS20")
    assert held.status_code == 422 and "redemption limit" in held.json()["detail"]

    cancelled = client.post(f"{API}/checkout/sessions/{first['id']}/cancel", headers=attendee)
    assert cancelled.json()["status"] == "cancelled"
    assert quote(client, attendee, event_id, [(standard, 1)], "CAMPUS20").is_success

    # An abandoned checkout releases its promo hold and inventory once it expires.
    abandoned = checkout(client, attendee, event_id, [(standard, 3)], "CAMPUS20").json()
    with Session(engine) as db:
        db.execute(
            update(Order)
            .where(Order.id == UUID(abandoned["id"]))
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        db.commit()
    order = checkout(client, attendee, event_id, [(standard, 1)], "CAMPUS20").json()
    assert complete(client, attendee, abandoned["id"]).status_code == 409
    assert complete(client, attendee, order["id"]).json()["status"] == "confirmed"

    types = client.get(f"{API}/events/{event_id}/ticket-types").json()
    standard_type = next(t for t in types if t["id"] == standard)
    assert standard_type["reserved_count"] == 0 and standard_type["sold_count"] == 1
    exhausted = quote(client, attendee, event_id, [(standard, 1)], "CAMPUS20").json()
    assert "redemption limit" in exhausted["detail"]

    stats = client.get(
        f"{API}/organizer/analytics/campaigns", headers=organizer, params={"event_id": event_id}
    ).json()
    assert stats[0]["id"] == created["id"]
    assert stats[0]["redemptions"] == 1 and stats[0]["orders"] == 1
    assert stats[0]["tickets_sold"] == 1
    assert stats[0]["gross_revenue_minor"] == 1_000_000
    assert stats[0]["discount_minor"] == 200_000
    assert stats[0]["net_revenue_minor"] == 800_000
    assert stats[0]["state"] == "exhausted"

    lowered = client.patch(
        f"{API}/events/{event_id}/campaigns/{created['id']}",
        headers=organizer,
        json={"max_redemptions": 0},
    )
    assert lowered.status_code == 422


def test_campaign_qr_resolution_disable_and_admission_rejection(client, engine):
    organizer, attendee, event_id, standard, *_ = paid_event(client, engine)
    created = campaign(client, organizer, event_id).json()
    base = f"{API}/events/{event_id}/campaigns/{created['id']}"

    qr = client.get(f"{base}/qr", headers=organizer)
    assert qr.headers["content-type"] == "image/png" and qr.content.startswith(b"\x89PNG")
    assert client.get(f"{base}/qr", headers=attendee).status_code == 403

    resolved = client.get(f"{API}/campaigns/resolve/{created['opaque_token']}").json()
    assert resolved["event_id"] == event_id
    assert resolved["promo_code"] == "CAMPUS20" and resolved["state"] == "active"
    assert client.get(f"{API}/campaigns/resolve/unknown-token").status_code == 404

    disabled = client.patch(base, headers=organizer, json={"enabled": False}).json()
    assert disabled["state"] == "disabled"
    resolved = client.get(f"{API}/campaigns/resolve/{created['opaque_token']}").json()
    assert resolved["promo_code"] is None and "disabled" in resolved["message"]
    assert client.get(base, headers=organizer).json()["visit_count"] == 2
    rejected = quote(client, attendee, event_id, [(standard, 1)], "CAMPUS20")
    assert rejected.status_code == 422 and "disabled" in rejected.json()["detail"]
    history = client.get(f"{API}/events/{event_id}/history", headers=organizer).json()
    assert {"campaign.created", "campaign.disabled"} <= {entry["action"] for entry in history}

    params = {"qr_payload": created["campaign_url"]}
    admit = client.post(f"{API}/check-in/events/{event_id}/admit", headers=organizer, params=params)
    assert admit.status_code == 409 and "not an admission ticket" in admit.json()["detail"]
    check = client.post(
        f"{API}/check-in/events/{event_id}/validate", headers=organizer, params=params
    ).json()
    assert check["reason"] == "CAMPAIGN_QR_NOT_ADMISSION"
