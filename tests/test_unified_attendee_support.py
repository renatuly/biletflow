"""Attendees use conversations while admins retain formal review controls."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_refund_support import scenario  # noqa: F401
from test_support_flows import queues as queues_fixture

from app.events.models import Event
from app.orders.models import Order, Refund
from app.platform.operations_models import EventReport, OrderDispute
from app.support.migrate import upgrade
from app.support.models import SupportCase, SupportMessage
from app.tickets.models import Ticket

queues = queues_fixture


def create(client, buyer, v, category, **extra):
    return client.post(
        "/api/v1/support/cases",
        headers={**buyer, "Idempotency-Key": "unified-" + category},
        json={
            "flow": "attendee",
            "category": category,
            "subject": "Please review this issue",
            "message": "Please check the information on my booking.",
            "event_id": v["event"],
            **extra,
        },
    )


def publish(engine, v):
    with Session(engine) as db:
        db.get(Event, UUID(v["event"])).status = "published"
        db.commit()


def test_event_report_is_one_private_conversation_with_explicit_admin_decision(queues):
    client, engine, v = queues
    owner, buyer, scanner, stranger = v["tokens"]
    publish(engine, v)
    response = create(client, buyer, v, "report_event")
    assert response.status_code == 201, response.text
    case = response.json()
    url = "/api/v1/support/cases/" + case["id"]
    assert case["routing"] == "platform" and case["formal_type"] == "report"
    assert create(client, buyer, v, "report_event").json()["id"] == case["id"]
    for user in (owner, scanner, stranger):
        assert client.get(url, headers=user).status_code == 403
        assert (
            case["id"]
            not in [
                c["id"] for c in client.get("/api/v1/support/cases?view=inbox", headers=user).json()
            ]
            if user != scanner
            else True
        )
    assert (
        client.patch(
            url + "/assignee", headers=v["admin"], json={"user_id": str(v["owner_id"])}
        ).status_code
        == 403
    )
    assert client.patch(url + "/status?status=resolved", headers=v["admin"]).status_code == 409
    records = client.get("/api/v1/admin/event-reports", headers=v["admin"]).json()
    assert records[0]["id"] == case["formal_id"]
    assert (
        client.post(
            url + "/messages", headers=v["admin"], json={"body": "We are checking the listing."}
        ).status_code
        == 201
    )
    decision = client.patch(
        "/api/v1/admin/event-reports/" + case["formal_id"],
        headers=v["admin"],
        json={"status": "dismissed", "resolution": "The published information has been verified."},
    )
    assert decision.status_code == 200, decision.text
    result = client.get(url, headers=buyer).json()
    assert result["status"] == "resolved" and result["formal_status"] == "dismissed"
    assert result["formal_resolution"] == "The published information has been verified."
    messages = client.get(url + "/messages", headers=buyer).json()
    assert any("published information has been verified" in m["body"] for m in messages)
    assert client.patch(url + "/status?status=open", headers=v["admin"]).status_code == 409
    activity = client.get("/api/v1/events/" + v["event"] + "/activity", headers=owner)
    assert activity.status_code == 200
    assert "The published information has been verified." not in activity.text
    with Session(engine) as db:
        assert db.scalar(select(func.count(EventReport.id))) == 1
        assert db.get(Event, UUID(v["event"])).status == "published"


def test_confirmed_payment_problem_opens_dispute_and_never_refunds_on_resolution(queues):
    client, engine, v = queues
    owner, buyer, scanner, stranger = v["tokens"]
    response = create(client, buyer, v, "payment", order_id=v["order"])
    assert response.status_code == 201, response.text
    case = response.json()
    url = "/api/v1/support/cases/" + case["id"]
    assert case["routing"] == "platform" and case["formal_type"] == "dispute"
    assert case["can_refund"] is False
    assert case["assigned_to"] is None
    assert client.get(url, headers=owner).status_code == 403
    assert (
        client.post(url + "/messages", headers=scanner, json={"body": "Unauthorized"}).status_code
        == 403
    )
    assert client.patch(url + "/status?status=resolved", headers=v["admin"]).status_code == 409
    result = client.patch(
        "/api/v1/admin/disputes/" + case["formal_id"],
        headers=v["admin"],
        json={"status": "resolved", "resolution": "Booking and simulated payment match."},
    )
    assert result.status_code == 200, result.text
    assert client.get(url, headers=buyer).json()["status"] == "resolved"
    notifications = client.get("/api/v1/notifications", headers=owner)
    assert "Booking and simulated payment match." not in notifications.text
    with Session(engine) as db:
        assert db.scalar(select(func.count(OrderDispute.id))) == 1
        assert db.get(Order, UUID(v["order"])).status == "confirmed"
        assert db.scalar(select(func.count(Refund.id))) == 0
        assert set(db.scalars(select(Ticket.status))) == {"valid"}


def test_failed_payment_help_is_platform_chat_without_a_formal_dispute(queues):
    client, engine, v = queues
    owner, buyer, scanner, stranger = v["tokens"]
    with Session(engine) as db:
        db.get(Order, UUID(v["order"])).status = "payment_failed"
        db.commit()
    response = create(client, buyer, v, "payment", order_id=v["order"])
    assert response.status_code == 201, response.text
    case = response.json()
    assert case["routing"] == "platform" and case["formal_id"] is None
    assert client.get("/api/v1/support/cases/" + case["id"], headers=owner).status_code == 403
    with Session(engine) as db:
        assert db.scalar(select(func.count(OrderDispute.id))) == 0
    assert create(client, stranger, v, "payment", order_id=v["order"]).status_code == 403


def test_existing_formal_histories_become_conversations_without_duplicates(queues):
    client, engine, v = queues
    owner, buyer, scanner, stranger = v["tokens"]
    with Session(engine) as db:
        report = EventReport(
            event_id=UUID(v["event"]),
            reporter_id=v["buyer_id"],
            reason="An older event report",
            status="dismissed",
            resolution="Earlier platform decision retained.",
        )
        dispute = OrderDispute(
            order_id=UUID(v["order"]),
            requester_id=v["buyer_id"],
            reason="An older payment review",
            status="under_review",
        )
        db.add_all([report, dispute])
        db.commit()
        report_id, dispute_id = report.id, dispute.id
    upgrade(engine)
    upgrade(engine)
    rows = client.get("/api/v1/support/cases?flow=attendee&view=mine", headers=buyer).json()
    assert len(rows) == 2
    report_case = next(r for r in rows if r["formal_type"] == "report")
    assert report_case["formal_resolution"] == "Earlier platform decision retained."
    assert report_case["status"] == "resolved"
    assert next(r for r in rows if r["formal_type"] == "dispute")["status"] == "in_progress"
    with Session(engine) as db:
        assert db.get(EventReport, report_id).resolution == "Earlier platform decision retained."
        assert db.get(OrderDispute, dispute_id).status == "under_review"
        assert db.scalar(select(func.count(SupportCase.id))) == 2
        assert db.scalar(select(func.count(SupportMessage.id))) == 2


def test_legacy_formal_submission_and_chat_share_the_same_case(queues):
    client, engine, v = queues
    owner, buyer, scanner, stranger = v["tokens"]
    publish(engine, v)
    response = client.post(
        "/api/v1/events/" + v["event"] + "/reports",
        headers=buyer,
        json={"reason": "Incorrect published information"},
    )
    assert response.status_code == 201, response.text
    rows = client.get("/api/v1/support/cases?view=mine", headers=buyer).json()
    assert len(rows) == 1 and rows[0]["formal_id"] == response.json()["id"]
    duplicate = create(client, buyer, v, "report_event")
    assert duplicate.status_code == 409
    with Session(engine) as db:
        assert db.scalar(select(func.count(EventReport.id))) == 1
        assert db.scalar(select(func.count(SupportCase.id))) == 1
