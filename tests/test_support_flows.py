from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import Session
from test_refund_support import scenario  # noqa: F401

from app.core.database import Base
from app.core.security import create_token
from app.orders.models import Order, Refund
from app.support.migrate import upgrade
from app.support.models import SupportCase, SupportChange, SupportMessage
from app.tickets.models import Ticket
from app.users.models import Notification, User


def token(user_id):
    return {"Authorization": f"Bearer {create_token(str(user_id))}"}


@pytest.fixture
def queues(scenario):  # noqa: F811 - pytest injects the imported fixture
    client, engine, v = scenario
    with Session(engine) as db:
        admin = User(
            email="platform@test.example",
            full_name="Platform Admin",
            password_hash="unused",
            roles=["platform_admin"],
        )
        admin2 = User(
            email="second-admin@test.example", password_hash="unused", roles=["platform_admin"]
        )
        other = User(
            email="other-organizer@test.example", password_hash="unused", roles=["organizer"]
        )
        db.add_all([admin, admin2, other])
        db.flush()
        v.update(
            admin=token(admin.id),
            admin2=token(admin2.id),
            other=token(other.id),
            admin_id=str(admin.id),
            admin2_id=str(admin2.id),
            other_id=str(other.id),
        )
        v["staff_id"] = str(
            db.scalar(select(User).where(User.email == "event_admin@test.example")).id
        )
        db.commit()
    return client, engine, v


def test_attendee_conversation_scope_assignment_replies_and_refund_separation(queues):
    client, engine, v = queues
    owner, buyer, scanner, stranger = v["tokens"]
    payload = {
        "flow": "attendee",
        "category": "refund",
        "subject": "Refund conversation",
        "message": "Please review my booking",
        "ticket_id": v["tickets"][0],
    }
    created = client.post(
        "/api/v1/support/cases", headers={**buyer, "Idempotency-Key": "create-case-1"}, json=payload
    )
    assert created.status_code == 201, created.text
    row = created.json()
    url = f"/api/v1/support/cases/{row['id']}"
    assert row["flow"] == "attendee" and row["routing"] == "organizer"
    assert row["event_id"] == v["event"] and row["order_id"] == v["order"]
    assert (
        client.post(
            "/api/v1/support/cases",
            headers={**buyer, "Idempotency-Key": "create-case-1"},
            json=payload,
        ).json()["id"]
        == row["id"]
    )
    assert (
        client.post(
            "/api/v1/support/cases",
            headers={**buyer, "Idempotency-Key": "create-case-1"},
            json={**payload, "message": "Different intent"},
        ).status_code
        == 409
    )
    for unauthorized in [scanner, stranger, v["other"]]:
        for suffix in ["", "/messages", "/history", "/assignees"]:
            assert client.get(url + suffix, headers=unauthorized).status_code == 403
        assert (
            client.patch(
                url + "/assignee", headers=unauthorized, json={"user_id": v["staff_id"]}
            ).status_code
            == 403
        )
    assert client.get("/api/v1/users/me", headers=scanner).json()["support_access"] is False
    assert (
        client.post(
            f"/api/v1/support/events/{v['event']}/staff",
            headers=scanner,
            json={"email": "stranger@test.example"},
        ).status_code
        == 403
    )
    grant = client.post(
        f"/api/v1/support/events/{v['event']}/staff",
        headers=owner,
        json={"email": "event_admin@test.example"},
    )
    assert grant.status_code == 201, grant.text
    assert client.get("/api/v1/users/me", headers=scanner).json()["support_access"] is True
    assert (
        len(client.get("/api/v1/support/cases?flow=attendee&view=inbox", headers=scanner).json())
        == 1
    )
    assert (
        client.get("/api/v1/support/cases?flow=attendee&view=inbox", headers=v["other"]).json()
        == []
    )
    assert {a["id"] for a in client.get(url + "/assignees", headers=owner).json()} == {
        str(v["owner_id"]),
        v["staff_id"],
    }
    assert (
        client.patch(url + "/assignee", headers=owner, json={"user_id": v["other_id"]}).status_code
        == 403
    )
    assert (
        client.patch(url + "/assignee", headers=buyer, json={"user_id": v["staff_id"]}).status_code
        == 403
    )
    assert (
        client.patch(url + "/assignee", headers=owner, json={"user_id": v["staff_id"]}).status_code
        == 200
    )
    assert (
        client.patch(url + "/status?status=waiting_for_customer", headers=scanner).status_code
        == 200
    )
    reply_headers = {**buyer, "Idempotency-Key": "message-1"}
    message = client.post(
        url + "/messages", headers=reply_headers, json={"body": "Here are the details"}
    )
    assert message.status_code == 201, message.text
    assert message.json()["sender_name"] and message.json()["created_at"]
    assert (
        client.post(
            url + "/messages", headers=reply_headers, json={"body": "Here are the details"}
        ).json()["id"]
        == message.json()["id"]
    )
    assert (
        client.post(
            url + "/messages", headers=reply_headers, json={"body": "Changed body"}
        ).status_code
        == 409
    )
    assert client.get(url, headers=buyer).json()["status"] == "in_progress"
    assert len(client.get(url + "/messages", headers=buyer).json()) == 2
    assert (
        client.post(
            url + "/messages", headers=scanner, json={"body": "Reviewing this now"}
        ).status_code
        == 201
    )
    assert client.patch(url + "/status?status=resolved", headers=scanner).status_code == 200
    detail = client.get(url, headers=buyer).json()
    assert detail["status"] == "resolved" and detail["order_status"] == "confirmed"
    assert detail["refund_status"] is None
    assert (
        client.post(url + "/messages", headers=buyer, json={"body": "Another message"}).status_code
        == 409
    )
    assert client.post(url + "/refund", headers=scanner).status_code == 403
    with Session(engine) as db:
        assert db.scalar(select(func.count(Refund.id))) == 0
        assert db.get(Order, UUID(v["order"])).status == "confirmed"
        assert db.get(Ticket, UUID(v["tickets"][0])).status == "valid"
        assert db.scalar(select(func.count(SupportMessage.id))) == 3
        assert db.scalar(select(func.count(SupportChange.id))) >= 5
        assert (
            db.scalar(
                select(func.count(Notification.id)).where(Notification.user_id == v["buyer_id"])
            )
            >= 1
        )
    # A separate organizer refund is still possible after resolving the conversation.
    assert client.post(url + "/refund", headers=owner).status_code == 200
    assert client.get(url, headers=buyer).json()["order_status"] == "refunded"
    assert client.get(url, headers=buyer).json()["refund_status"] == "succeeded"
    assert client.get(url, headers=buyer).json()["refund_is_simulation"] is True
    assert (
        client.delete(
            f"/api/v1/support/events/{v['event']}/staff/{v['staff_id']}", headers=owner
        ).status_code
        == 200
    )
    assert client.get(url, headers=scanner).status_code == 403  # assignment alone grants no access
    assert client.get(url + "/messages", headers=scanner).status_code == 403


def test_organizer_platform_queue_is_separate_and_admin_only(queues):
    client, engine, v = queues
    owner, buyer, scanner, stranger = v["tokens"]
    body = {
        "flow": "organizer_platform",
        "category": "activation",
        "subject": "Paid sales activation",
        "message": "Please help activate paid sales",
        "event_id": v["event"],
    }
    assert client.post("/api/v1/support/cases", headers=buyer, json=body).status_code == 403
    assert client.post("/api/v1/support/cases", headers=v["other"], json=body).status_code == 403
    created = client.post("/api/v1/support/cases", headers=owner, json=body)
    assert created.status_code == 201, created.text
    row = created.json()
    url = f"/api/v1/support/cases/{row['id']}"
    assert row["routing"] == "platform" and row["flow"] == "organizer_platform"
    assert (
        client.get("/api/v1/support/cases?flow=attendee&view=inbox", headers=v["admin"]).json()
        == []
    )
    assert (
        len(
            client.get(
                "/api/v1/support/cases?flow=organizer_platform&view=inbox", headers=v["admin"]
            ).json()
        )
        == 1
    )
    assert (
        len(
            client.get(
                "/api/v1/support/cases?flow=organizer_platform&view=mine", headers=owner
            ).json()
        )
        == 1
    )
    for actor in [buyer, scanner, stranger, v["other"]]:
        assert client.get(url, headers=actor).status_code == 403
        assert (
            client.post(url + "/messages", headers=actor, json={"body": "Read this"}).status_code
            == 403
        )
    assert client.patch(url + "/status?status=resolved", headers=owner).status_code == 403
    assert (
        client.patch(
            url + "/assignee", headers=v["admin"], json={"user_id": str(v["owner_id"])}
        ).status_code
        == 403
    )
    assert (
        client.patch(
            url + "/assignee", headers=v["admin"], json={"user_id": v["admin2_id"]}
        ).status_code
        == 200
    )
    assert (
        client.patch(url + "/status?status=waiting_for_customer", headers=v["admin2"]).status_code
        == 200
    )
    assert (
        client.post(
            url + "/messages", headers=owner, json={"body": "The activation fee failed"}
        ).status_code
        == 201
    )
    assert client.get(url, headers=owner).json()["status"] == "in_progress"
    assert (
        client.post(
            url + "/messages", headers=v["admin2"], json={"body": "Please retry the sandbox fee"}
        ).status_code
        == 201
    )
    assert client.patch(url + "/status?status=resolved", headers=v["admin2"]).status_code == 200
    assert len(client.get(url + "/messages", headers=owner).json()) == 3
    assert client.get(url + "/history", headers=owner).json()[-1]["new_value"] == "resolved"
    with Session(engine) as db:
        assert (
            db.scalar(
                select(func.count(Notification.id)).where(Notification.user_id == v["owner_id"])
            )
            >= 2
        )


def test_no_event_falls_back_to_platform_and_assignment_cannot_widen_scope(queues):
    client, engine, v = queues
    owner, buyer, scanner, stranger = v["tokens"]
    created = client.post(
        "/api/v1/support/cases",
        headers=buyer,
        json={
            "flow": "attendee",
            "category": "account",
            "subject": "Account question",
            "message": "How can I update my account?",
        },
    )
    assert created.status_code == 201, created.text
    row = created.json()
    url = f"/api/v1/support/cases/{row['id']}"
    assert row["routing"] == "platform"
    assert client.get(url, headers=owner).status_code == 403
    assert client.get(url, headers=v["admin"]).status_code == 200
    assert (
        client.patch(
            url + "/assignee", headers=v["admin"], json={"user_id": str(v["owner_id"])}
        ).status_code
        == 403
    )
    with Session(engine) as db:
        db.get(SupportCase, UUID(row["id"])).assigned_to = v["owner_id"]
        db.commit()
    assert client.get(url, headers=owner).status_code == 403
    assert client.get("/api/v1/support/cases?flow=attendee&view=inbox", headers=owner).json() == []
    assert (
        client.post(
            "/api/v1/support/cases",
            headers=buyer,
            json={
                "flow": "attendee",
                "category": "activation",
                "subject": "Invalid category",
                "message": "Wrong flow",
            },
        ).status_code
        == 422
    )


def test_additive_migration_preserves_legacy_cases_and_messages():
    engine = create_engine("sqlite://")
    tables = [t for t in Base.metadata.sorted_tables if not t.name.startswith("support_")]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as db:
        user = User(email="legacy@test.example", password_hash="unused", roles=["organizer"])
        db.add(user)
        db.commit()
        uid = user.id
    from uuid import uuid4

    cid, mid = uuid4(), uuid4()
    now = datetime.now(UTC).isoformat()
    with engine.begin() as c:
        c.execute(
            text(
                "CREATE TABLE support_cases (id CHAR(32) PRIMARY KEY, requester_id CHAR(32), "
                "event_id CHAR(32), order_id CHAR(32), ticket_id CHAR(32), assigned_to "
                "CHAR(32), category VARCHAR(50), status VARCHAR(30), subject VARCHAR(200), "
                "created_at DATETIME, updated_at DATETIME)"
            )
        )
        c.execute(
            text(
                "CREATE TABLE support_messages (id CHAR(32) PRIMARY KEY, case_id CHAR(32), "
                "sender_id CHAR(32), body TEXT, created_at DATETIME, updated_at DATETIME)"
            )
        )
        c.execute(
            text(
                "INSERT INTO support_cases (id, requester_id, category, status, subject, "
                "created_at, updated_at) VALUES (:id,:uid,'account','open','Legacy account "
                "case',:now,:now)"
            ),
            {"id": cid.hex, "uid": uid.hex, "now": now},
        )
        c.execute(
            text(
                "INSERT INTO support_messages VALUES (:id,:cid,:uid,'History survives',:now,:now)"
            ),
            {"id": mid.hex, "cid": cid.hex, "uid": uid.hex, "now": now},
        )
    Base.metadata.create_all(engine)
    upgrade(engine)
    upgrade(engine)
    with Session(engine) as db:
        assert db.get(SupportCase, cid).flow == "organizer_platform"
        assert db.get(SupportMessage, mid).body == "History survives"
    assert "client_key" in {c["name"] for c in inspect(engine).get_columns("support_messages")}
    engine.dispose()


def test_support_only_grant_does_not_grant_scanning_or_unrelated_event_management(queues):
    client, engine, v = queues
    owner, buyer, scanner, stranger = v["tokens"]
    granted = client.post(
        f"/api/v1/support/events/{v['event']}/staff",
        headers=owner,
        json={"email": "stranger@test.example"},
    )
    assert granted.status_code == 201
    # The same token reads current roles and explicit grants from persistence.
    assert client.get("/api/v1/users/me", headers=stranger).json()["support_access"] is True
    assert client.get("/api/v1/check-in/events", headers=stranger).json() == []
    assert (
        client.post(
            f"/api/v1/check-in/events/{v['event']}/tickets/{v['tickets'][0]}/admit",
            headers=stranger,
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/v1/events/{v['event']}", headers=stranger, json={"title": "Changed"}
        ).status_code
        == 403
    )
    assert client.get(f"/api/v1/events/{v['event']}/orders", headers=stranger).status_code == 403


def test_legacy_forged_context_does_not_expose_or_refund_another_buyers_order(queues):
    client, engine, v = queues
    owner, buyer, scanner, stranger = v["tokens"]
    with Session(engine) as db:
        requester = db.scalar(select(User).where(User.email == "stranger@test.example"))
        case = SupportCase(
            requester_id=requester.id,
            flow="attendee",
            event_id=UUID(v["event"]),
            order_id=UUID(v["order"]),
            category="refund",
            subject="Forged legacy case",
        )
        db.add(case)
        db.commit()
        case_id = str(case.id)
    url = f"/api/v1/support/cases/{case_id}"
    result = client.get(url, headers=stranger)
    assert result.status_code == 200
    assert result.json()["order_id"] is None
    assert result.json()["order_total_minor"] is None
    assert client.post(url + "/refund", headers=owner).status_code == 409
    with Session(engine) as db:
        assert db.scalar(select(func.count(Refund.id))) == 0


def test_scanning_staff_can_contact_platform_without_attendee_support_access(queues):
    client, engine, v = queues
    owner, buyer, scanner, stranger = v["tokens"]
    response = client.post("/api/v1/support/cases", headers=scanner, json={
        "flow": "organizer_platform", "category": "technical",
        "subject": "Scanner problem", "message": "Please help with my scanner",
    })
    assert response.status_code == 201, response.text
    case_id = response.json()["id"]
    mine = client.get("/api/v1/support/cases?flow=organizer_platform&view=mine", headers=scanner)
    assert mine.status_code == 200, mine.text
    assert case_id in [case["id"] for case in mine.json()]
    assert client.get("/api/v1/support/cases?flow=attendee&view=inbox", headers=scanner).status_code == 403
    assert client.get(f"/api/v1/support/cases/{case_id}", headers=owner).status_code == 403
    queue = client.get("/api/v1/support/cases?flow=organizer_platform&view=inbox", headers=v["admin"])
    assert case_id in [case["id"] for case in queue.json()]
