from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_qr_credential, create_token
from app.events.models import Event, StaffAssignment, Venue
from app.main import app
from app.orders.models import Order, OrderItem, Refund
from app.support.models import SupportCase
from app.tickets.models import Ticket, TicketType
from app.users.models import Notification, User


@pytest.fixture
def scenario():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        users = [
            User(email=f"{role}@test.example", password_hash="unused", roles=[role])
            for role in ["organizer", "attendee", "event_admin"]
        ]
        stranger = User(email="stranger@test.example", password_hash="unused", roles=["attendee"])
        venue = Venue(name="Hall", address="Abay 1", city="Almaty")
        db.add_all([*users, stranger, venue])
        db.flush()
        owner, buyer, staff = users
        now = datetime.now(UTC)
        event = Event(
            organizer_id=owner.id,
            venue_id=venue.id,
            title="Paid concert",
            description="Description",
            category="music",
            starts_at=now + timedelta(days=2),
            ends_at=now + timedelta(days=2, hours=2),
            registration_opens_at=now,
            registration_closes_at=now + timedelta(days=1),
            capacity=20,
            refund_policy="Request before event start",
        )
        db.add(event)
        db.flush()
        db.add(StaffAssignment(event_id=event.id, user_id=staff.id))
        kind = TicketType(
            event_id=event.id,
            name="General",
            kind="paid",
            price_minor=100000,
            quantity=20,
            sold_count=2,
            sales_start=now,
            sales_end=now + timedelta(days=1),
        )
        db.add(kind)
        db.flush()
        order = Order(
            event_id=event.id,
            user_id=buyer.id,
            attendee_name="Buyer",
            attendee_email=buyer.email,
            status="confirmed",
            total_minor=200000,
            expires_at=now + timedelta(days=1),
        )
        db.add(order)
        db.flush()
        item = OrderItem(
            order_id=order.id, ticket_type_id=kind.id, quantity=2, unit_price_minor=100000
        )
        db.add(item)
        db.flush()
        tickets = []
        for i in range(2):
            from uuid import uuid4

            ticket_id = uuid4()
            ticket = Ticket(
                id=ticket_id,
                order_item_id=item.id,
                event_id=event.id,
                ticket_type_id=kind.id,
                owner_id=buyer.id,
                attendee_name=f"Guest {i}",
                attendee_email=buyer.email,
                qr_credential=create_qr_credential(str(ticket_id)),
            )
            db.add(ticket)
            tickets.append(str(ticket_id))
        values = {
            "event": str(event.id),
            "order": str(order.id),
            "kind": str(kind.id),
            "tickets": tickets,
            "buyer_id": buyer.id,
            "owner_id": owner.id,
            "tokens": [
                {"Authorization": f"Bearer {create_token(str(u.id))}"}
                for u in [owner, buyer, staff, stranger]
            ],
        }
        db.commit()

    def database():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = database
    try:
        with TestClient(app) as client:
            yield client, engine, values
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


@pytest.mark.parametrize("approval", ["support", "order"])
def test_refund_request_approval_resolves_case_invalidates_all_tickets_and_notifies(
    scenario, approval
):
    client, engine, v = scenario
    owner, buyer, staff, stranger = v["tokens"]
    prefix = "/api/v1"
    payload = {
        "category": "refund",
        "subject": "Please refund order",
        "message": "Unable to attend",
        "event_id": v["event"],
        "order_id": v["order"],
        "ticket_id": v["tickets"][0],
    }
    assert client.post(prefix + "/support/cases", json=payload, headers=stranger).status_code == 403
    mismatched = {**payload, "event_id": "00000000-0000-0000-0000-000000000001"}
    assert client.post(prefix + "/support/cases", json=mismatched, headers=buyer).status_code == 422
    created = client.post(prefix + "/support/cases", json=payload, headers=buyer)
    assert created.status_code == 201, created.text
    case = created.json()
    case_id = case["id"]
    url = f"{prefix}/support/cases/{case_id}"
    assert (
        client.post(prefix + "/support/cases", json=payload, headers=buyer).json()["id"] == case_id
    )
    assert client.get(prefix + "/tickets", headers=buyer).json()[0]["order_id"] == v["order"]
    assert client.get(prefix + "/tickets", headers=buyer).json()[0]["can_request_refund"] is True
    assert client.get(prefix + "/support/cases", headers=owner).json()[0]["can_refund"] is True
    assert client.get(url, headers=stranger).status_code == 403
    assert client.post(url + "/refund", headers=buyer).status_code == 403
    for method, path, body in [
        ("get", "/support/cases", None),
        ("get", f"/support/cases/{case_id}/messages", None),
        ("get", f"/support/cases/{case_id}", None),
        ("post", "/support/cases", payload),
        ("post", f"/support/cases/{case_id}/messages", {"body": "reply"}),
        ("patch", f"/support/cases/{case_id}/status?status=resolved", None),
        ("post", f"/support/cases/{case_id}/refund", None),
    ]:
        assert client.request(method, prefix + path, json=body, headers=staff).status_code == 403
    assert (
        client.post(
            f"{prefix}/orders/{v['order']}/refunds", json={"reason": "test refund"}, headers=staff
        ).status_code
        == 403
    )
    checked = client.post(
        f"{prefix}/check-in/events/{v['event']}/tickets/{v['tickets'][0]}/admit",
        headers=staff,
    )
    assert checked.status_code == 200
    if approval == "support":
        result = client.post(url + "/refund", headers=owner)
    else:
        result = client.post(
            f"{prefix}/orders/{v['order']}/refunds",
            json={"reason": "Approved from web"},
            headers=owner,
        )
    assert result.status_code in [200, 201], result.text
    for headers in [owner, buyer]:
        assert client.get(url, headers=headers).json()["status"] == "resolved"
        assert (
            client.get(prefix + "/support/cases", headers=headers).json()[0]["status"] == "resolved"
        )
    assert client.post(url + "/refund", headers=owner).status_code == 200
    assert client.patch(url + "/status?status=open", headers=owner).status_code == 409
    assert (
        client.post(url + "/messages", json={"body": "new reply"}, headers=buyer).status_code == 409
    )
    for ticket in client.get(prefix + "/tickets", headers=buyer).json():
        assert ticket["status"] == "refunded"
        assert ticket["can_request_refund"] is False
        assert (
            client.post(
                f"{prefix}/check-in/events/{v['event']}/admit",
                params={"qr_payload": ticket["qr_credential"]},
                headers=staff,
            ).status_code
            == 409
        )
    assert (
        client.get(url + "/messages", headers=buyer)
        .json()[-1]["body"]
        .startswith("Refund approved")
    )
    with Session(engine) as db:
        assert db.scalar(select(func.count(Refund.id))) == 1
        assert db.scalar(select(func.count(SupportCase.id))) == 1
        assert (
            db.scalar(
                select(func.count(Notification.id)).where(
                    Notification.user_id == v["buyer_id"], Notification.kind == "refund_completion"
                )
            )
            == 1
        )
        kind = db.get(TicketType, __import__("uuid").UUID(v["kind"]))
        assert kind.sold_count == 0 and kind.refunded_count == 2
        assert (
            db.scalar(select(Notification).where(Notification.user_id == v["owner_id"])).kind
            == "support_request"
        )


def test_policy_required_for_paid_types_optional_for_free_and_cannot_be_cleared(scenario):
    client, engine, v = scenario
    owner = v["tokens"][0]
    event_url = f"/api/v1/events/{v['event']}"
    assert client.patch(event_url, json={"refund_policy": "   "}, headers=owner).status_code == 422
    with Session(engine) as db:
        kind = db.get(TicketType, __import__("uuid").UUID(v["kind"]))
        kind.kind = "free"
        kind.price_minor = 0
        db.get(Event, __import__("uuid").UUID(v["event"])).capacity = 22
        db.commit()
    assert client.patch(event_url, json={"refund_policy": ""}, headers=owner).status_code == 200
    now = datetime.now(UTC)
    data = {
        "name": "Paid admission",
        "kind": "paid",
        "price_minor": 100000,
        "quantity": 1,
        "sales_start": now.isoformat(),
        "sales_end": (now + timedelta(hours=1)).isoformat(),
    }
    assert client.post(event_url + "/ticket-types", json=data, headers=owner).status_code == 422
    assert (
        client.post(
            event_url + "/ticket-types",
            json={**data, "kind": "free", "price_minor": 0},
            headers=owner,
        ).status_code
        == 201
    )
    assert (
        client.patch(
            event_url, json={"refund_policy": "Refund before event start"}, headers=owner
        ).status_code
        == 200
    )
    assert client.post(event_url + "/ticket-types", json=data, headers=owner).status_code == 201


def test_contact_organizer_routes_ticket_conversation_and_resolves_for_both_users(scenario):
    client, engine, v = scenario
    owner, buyer, staff, stranger = v["tokens"]
    created = client.post(
        "/api/v1/support/cases",
        headers=buyer,
        json={
            "category": "event_information",
            "subject": "Ask about the venue",
            "message": "Where is the entrance?",
            "ticket_id": v["tickets"][0],
        },
    )
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["event_id"] == v["event"] and row["order_id"] == v["order"]
    assert row["assigned_to"] == str(v["owner_id"])
    url = f"/api/v1/support/cases/{row['id']}"
    assert client.patch(url + "/status?status=resolved", headers=buyer).status_code == 403
    assert (
        client.post(
            url + "/messages", headers=owner, json={"body": "Use the main entrance."}
        ).status_code
        == 201
    )
    assert (
        client.get(url + "/messages", headers=buyer).json()[-1]["body"] == "Use the main entrance."
    )
    assert client.patch(url + "/status?status=resolved", headers=owner).status_code == 200
    assert client.get(url, headers=buyer).json()["status"] == "resolved"
    assert client.get(url, headers=owner).json()["status"] == "resolved"
    assert client.post(url + "/refund", headers=owner).status_code == 409
    assert (
        client.patch(
            f"/api/v1/events/{v['event']}", headers=staff, json={"refund_policy": "No refunds"}
        ).status_code
        == 403
    )
    with Session(engine) as db:
        assert db.scalar(select(func.count(Refund.id))) == 0
        assert (
            db.scalar(
                select(func.count(Notification.id)).where(
                    Notification.user_id == v["buyer_id"], Notification.kind == "support_message"
                )
            )
            == 1
        )


def test_admission_checks_current_status_even_when_session_cached_a_valid_ticket(scenario):
    import asyncio
    from uuid import UUID

    from fastapi import HTTPException

    from app.tickets.router import admit

    client, engine, v = scenario
    with Session(engine, expire_on_commit=False) as cached:
        ticket = cached.get(Ticket, UUID(v["tickets"][0]))
        assert ticket.status == "valid"
        staff = cached.scalar(select(User).where(User.email == "event_admin@test.example"))
        with Session(engine) as changed:
            changed.get(Ticket, ticket.id).status = "refunded"
            changed.commit()
        with pytest.raises(HTTPException) as error:
            asyncio.run(admit(UUID(v["event"]), ticket.qr_credential, None, staff, cached))
        assert error.value.status_code == 409
