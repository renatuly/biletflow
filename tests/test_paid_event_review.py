from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_refund_support import scenario  # noqa: F401

from app.core.security import create_token
from app.events.models import Event, OrganizerProfile
from app.events.paid_applications import PaidEventApplication
from app.orders.models import Order
from app.users.models import Notification, User


@pytest.fixture
def paid(scenario):  # noqa: F811
    client, engine, v = scenario
    with Session(engine) as db:
        admin = User(
            email="paid-admin@test.example", password_hash="unused", roles=["platform_admin"]
        )
        db.add(admin)
        db.flush()
        v["admin"] = {"Authorization": f"Bearer {create_token(str(admin.id))}"}
        db.add(
            OrganizerProfile(
                user_id=v["owner_id"],
                contact_name="Organizer",
                contact_phone="+77000000000",
                verification_status="verified",
                payout_verified=True,
                payout_account_masked="****1234",
            )
        )
        db.commit()
    return client, engine, v


def prepare(client, v):
    owner = v["tokens"][0]
    path = f"/api/v1/events/{v['event']}/paid-sales"
    assert client.post(path + "/activation-fee", headers=owner).status_code == 200
    assert client.post(path + "/accept-terms", headers=owner).status_code == 200
    return path


def test_paid_event_requires_manual_review_before_publish_and_checkout(paid):
    client, engine, v = paid
    owner, buyer, staff, stranger = v["tokens"]
    event = f"/api/v1/events/{v['event']}"
    assert client.post(event + "/publish", headers=owner).status_code == 409
    assert client.post(event + "/paid-sales/apply", headers=owner).status_code == 409
    path = prepare(client, v)
    assert client.post(path + "/apply", headers=staff).status_code == 403
    submitted = client.post(path + "/apply", headers=owner)
    assert submitted.json()["status"] == "pending_review"
    assert (
        client.get("/api/v1/organizer/events", headers=owner).json()[0]["paid_review_status"]
        == "pending_review"
    )
    assert client.post(path + "/activate", headers=owner).json()["status"] == "pending_review"
    assert client.post(event + "/publish", headers=owner).status_code == 409
    assert client.get("/api/v1/admin/paid-event-applications", headers=owner).status_code == 403
    rows = client.get("/api/v1/admin/paid-event-applications", headers=v["admin"]).json()
    assert len(rows) == 1 and rows[0]["activation_fee_paid"] is True
    assert rows[0]["profile"]["payout_account_masked"] == "****1234"
    decision = f"/api/v1/admin/paid-event-applications/{v['event']}/approve"
    assert client.post(decision, headers=owner, json={}).status_code == 403
    assert client.post(decision, headers=v["admin"], json={}).status_code == 200
    with Session(engine) as db:
        assert db.get(Event, UUID(v["event"])).status == "draft"
    assert (
        client.get("/api/v1/organizer/events", headers=owner).json()[0]["paid_review_status"]
        == "approved"
    )
    with Session(engine) as db:
        assert db.scalar(select(PaidEventApplication)).reviewed_by is not None
        assert (
            db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.kind == "paid_event_review")
            )
            == 1
        )
    assert client.post(event + "/publish", headers=owner).status_code == 200
    checkout = client.post(
        "/api/v1/checkout/sessions",
        headers=buyer,
        json={
            "event_id": v["event"],
            "attendee_name": "Buyer",
            "attendee_email": "buyer@example.com",
            "items": [{"ticket_type_id": v["kind"], "quantity": 1}],
        },
    )
    assert checkout.status_code == 201, checkout.text


def test_rejection_requires_reason_and_allows_resubmission_without_fee_again(paid):
    client, engine, v = paid
    owner = v["tokens"][0]
    path = prepare(client, v)
    client.post(path + "/apply", headers=owner)
    decision = f"/api/v1/admin/paid-event-applications/{v['event']}/reject"
    assert client.post(decision, headers=v["admin"], json={}).status_code == 422
    assert (
        client.post(
            decision, headers=v["admin"], json={"reason": "Clarify venue access"}
        ).status_code
        == 200
    )
    checklist = client.get(path, headers=owner).json()
    assert checklist["review_status"] == "rejected"
    assert (
        client.get("/api/v1/organizer/events", headers=owner).json()[0]["paid_review_status"]
        == "rejected"
    )
    assert checklist["review_reason"] == "Clarify venue access"
    assert checklist["checklist"]["activation_fee_paid"] is True
    assert client.post(path + "/apply", headers=owner).json()["status"] == "pending_review"
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(PaidEventApplication)) == 1


def test_editing_reviewed_event_requires_new_review(paid):
    client, engine, v = paid
    owner = v["tokens"][0]
    path = prepare(client, v)
    client.post(path + "/apply", headers=owner)
    admin_path = f"/api/v1/admin/paid-event-applications/{v['event']}/approve"
    client.post(admin_path, headers=v["admin"], json={})
    changed = client.patch(
        f"/api/v1/events/{v['event']}", headers=owner, json={"title": "Updated paid event"}
    )
    assert changed.status_code == 200
    assert client.get(path, headers=owner).json()["review_status"] == "changes_required"
    assert client.post(f"/api/v1/events/{v['event']}/publish", headers=owner).status_code == 409
    assert client.post(admin_path, headers=v["admin"], json={}).status_code == 409
    assert client.post(path + "/apply", headers=owner).status_code == 200
    assert client.post(admin_path, headers=v["admin"], json={}).status_code == 200
    assert client.post(f"/api/v1/events/{v['event']}/publish", headers=owner).status_code == 200


def test_fee_alone_and_legacy_active_flag_cannot_bypass_review(paid):
    client, engine, v = paid
    owner, buyer, _, _ = v["tokens"]
    prepare(client, v)
    with Session(engine) as db:
        event = db.get(Event, UUID(v["event"]))
        event.paid_sales_status = "active"
        event.status = "published"
        db.commit()
    denied = client.post(
        "/api/v1/checkout/sessions",
        headers=buyer,
        json={
            "event_id": v["event"],
            "attendee_name": "Buyer",
            "attendee_email": "buyer@example.com",
            "items": [{"ticket_type_id": v["kind"], "quantity": 1}],
        },
    )
    assert denied.status_code == 409, denied.text
    assert (
        client.get("/api/v1/organizer/events", headers=owner).json()[0]["paid_review_status"]
        == "not_submitted"
    )
    summary = client.get("/api/v1/admin/events/summary", headers=v["admin"]).json()
    assert summary["events_total"] == 1 and summary["tickets_issued"] == 2
    assert summary["confirmed_order_total_minor"] == 200000


def test_pending_changes_and_self_review_cannot_bypass_decision(paid):
    client, engine, v = paid
    owner = v["tokens"][0]
    path = prepare(client, v)
    client.post(path + "/apply", headers=owner)
    client.patch(
        f"/api/v1/events/{v['event']}", headers=owner, json={"description": "Updated description"}
    )
    decision = f"/api/v1/admin/paid-event-applications/{v['event']}/approve"
    assert client.post(decision, headers=v["admin"], json={}).status_code == 409
    client.post(path + "/apply", headers=owner)
    with Session(engine) as db:
        user = db.get(User, v["owner_id"])
        user.roles = ["organizer", "platform_admin"]
        db.commit()
    assert client.post(decision, headers=owner, json={}).status_code == 403
    assert client.post(decision, headers=v["admin"], json={}).status_code == 200


def test_existing_checkout_cannot_complete_after_paid_sales_suspension(paid):
    client, engine, v = paid
    owner, buyer, _, _ = v["tokens"]
    path = prepare(client, v)
    client.post(path + "/apply", headers=owner)
    client.post(
        f"/api/v1/admin/paid-event-applications/{v['event']}/approve", headers=v["admin"], json={}
    )
    client.post(f"/api/v1/events/{v['event']}/publish", headers=owner)
    checkout = client.post(
        "/api/v1/checkout/sessions",
        headers=buyer,
        json={
            "event_id": v["event"],
            "attendee_name": "Buyer",
            "attendee_email": "buyer@example.com",
            "items": [{"ticket_type_id": v["kind"], "quantity": 1}],
        },
    )
    assert checkout.status_code == 201, checkout.text
    client.post(f"/api/v1/admin/events/{v['event']}/suspend", headers=v["admin"])
    response = client.post(
        f"/api/v1/checkout/sessions/{checkout.json()['id']}/complete",
        headers=buyer,
        json={"outcome": "success"},
    )
    assert response.status_code == 409, response.text
    with Session(engine) as db:
        assert db.get(Order, UUID(checkout.json()["id"])).status == "pending"
