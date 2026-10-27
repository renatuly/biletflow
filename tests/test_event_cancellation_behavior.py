from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_refund_support import scenario  # noqa: F401

from app.events.models import Event
from app.orders.models import Order, Refund
from app.tickets.models import CheckInRecord, Ticket
from app.users.models import Notification


def test_cancel_invalidates_unused_tickets_notifies_once_and_refunds_separately(scenario):  # noqa: F811 - pytest injects the imported fixture
    client, engine, v = scenario
    owner, buyer, staff, stranger = v["tokens"]
    path = f"/api/v1/events/{v['event']}/cancel"
    assert client.post(path, headers=stranger).status_code == 403
    assert client.post(path, headers=staff).status_code == 403
    assert client.post(path, headers=owner).status_code == 200
    assert client.post(path, headers=owner).status_code == 200
    with Session(engine) as db:
        assert db.get(Event, UUID(v["event"])).status == "cancelled"
        assert db.get(Event, UUID(v["event"])).paid_sales_status == "suspended"
        assert {t.status for t in db.scalars(select(Ticket))} == {"cancelled"}
        assert db.get(Order, UUID(v["order"])).status == "confirmed"
        assert db.scalar(select(func.count()).select_from(Refund)) == 0
        notices = list(
            db.scalars(select(Notification).where(Notification.kind == "event_cancellation"))
        )
        assert len(notices) == 1
        assert notices[0].user_id == v["buyer_id"]
        assert notices[0].data["event_id"] == v["event"]
        assert "Refunds are handled separately" in notices[0].message
    tickets = client.get("/api/v1/tickets", headers=buyer).json()
    assert all(t["status"] == "cancelled" and t["can_request_refund"] for t in tickets)
    response = client.post(
        f"/api/v1/orders/{v['order']}/refunds", headers=owner, json={"reason": "Event cancelled"}
    )
    assert response.status_code == 201, response.text
    assert all(
        t["status"] == "refunded" for t in client.get("/api/v1/tickets", headers=buyer).json()
    )


def test_cancelled_event_blocks_legacy_qr_admission_manual_checkin_and_exports(scenario):  # noqa: F811 - pytest injects the imported fixture
    client, engine, v = scenario
    owner, buyer, staff, stranger = v["tokens"]
    client.post(f"/api/v1/events/{v['event']}/cancel", headers=owner)
    with Session(engine) as db:
        ticket = db.get(Ticket, UUID(v["tickets"][0]))
        ticket.status = "valid"  # Legacy/mismatched data must still be denied.
        qr = ticket.qr_credential
        db.commit()
    params = {"qr_payload": qr}
    result = client.post(
        f"/api/v1/check-in/events/{v['event']}/validate", headers=owner, params=params
    )
    assert result.json()["result"] == "cancelled"
    assert (
        client.post(
            f"/api/v1/check-in/events/{v['event']}/admit", headers=owner, params=params
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/check-in/events/{v['event']}/tickets/{v['tickets'][0]}/admit", headers=owner
        ).status_code
        == 409
    )
    for suffix in ("qr", "pdf"):
        assert (
            client.get(f"/api/v1/tickets/{v['tickets'][0]}/{suffix}", headers=buyer).status_code
            == 409
        )
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(CheckInRecord)) == 0


def test_cancellation_preserves_attendance_and_refunds_and_reversal_does_not_revalidate(scenario):  # noqa: F811 - pytest injects the imported fixture
    client, engine, v = scenario
    owner = v["tokens"][0]
    with Session(engine) as db:
        first = db.get(Ticket, UUID(v["tickets"][0]))
        first.status = "checked_in"
        db.get(Ticket, UUID(v["tickets"][1])).status = "refunded"
        record = CheckInRecord(
            ticket_id=first.id, event_id=first.event_id, staff_user_id=v["owner_id"]
        )
        db.add(record)
        db.flush()
        record_id = record.id
        db.commit()
    client.post(f"/api/v1/events/{v['event']}/cancel", headers=owner)
    with Session(engine) as db:
        assert db.get(Ticket, UUID(v["tickets"][0])).status == "checked_in"
        assert db.get(Ticket, UUID(v["tickets"][1])).status == "refunded"
        assert db.get(CheckInRecord, record_id).reversed_at is None
    reverse = client.post(f"/api/v1/check-in/records/{record_id}/reverse", headers=owner)
    assert reverse.status_code == 200, reverse.text
    with Session(engine) as db:
        assert db.get(Ticket, UUID(v["tickets"][0])).status == "cancelled"


def test_cancelled_event_cannot_complete_an_existing_pending_checkout(scenario):  # noqa: F811 - pytest injects the imported fixture
    client, engine, v = scenario
    owner, buyer, _, _ = v["tokens"]
    with Session(engine) as db:
        event = db.get(Event, UUID(v["event"]))
        event.status = "published"
        event.paid_sales_status = "active"
        db.commit()
    started = client.post(
        "/api/v1/checkout/sessions",
        headers=buyer,
        json={
            "event_id": v["event"],
            "attendee_name": "Buyer",
            "attendee_email": "attendee@test.example",
            "items": [{"ticket_type_id": v["kind"], "quantity": 1}],
        },
    )
    assert started.status_code == 201, started.text
    client.post(f"/api/v1/events/{v['event']}/cancel", headers=owner)
    completed = client.post(
        f"/api/v1/checkout/sessions/{started.json()['id']}/complete",
        headers=buyer,
        json={"outcome": "success"},
    )
    assert completed.status_code == 409, completed.text
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Ticket)) == 2
