from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_event_management import admin_headers
from test_refund_support import scenario  # noqa: F401

from app.events.models import Event, Venue
from app.events.paid_applications import PaidEventApplication, fingerprint
from app.orders.models import Order, Payment
from app.orders.reservations import expire_event
from app.platform.operations_models import EventInvitation
from app.tickets.models import TicketType
from app.users.models import Notification


def ready(engine, v, *, free=False, private=False):
    with Session(engine) as db:
        e = db.get(Event, UUID(v["event"]))
        e.status = "published"
        e.visibility = "private" if private else "public"
        kind = db.get(TicketType, UUID(v["kind"]))
        if free:
            kind.kind, kind.price_minor = "free", 0
        else:
            e.paid_sales_status = "active"
            db.add(
                PaidEventApplication(
                    event_id=e.id,
                    status="approved",
                    snapshot_hash=fingerprint(db, e),
                    submitted_at=datetime.now(UTC),
                )
            )
        db.commit()


def checkout(client, v, headers, key=None, quantity=1):
    return client.post(
        "/api/v1/checkout/sessions",
        headers={**headers, **({"Idempotency-Key": key} if key else {})},
        json={
            "event_id": v["event"],
            "attendee_name": "Invited Guest",
            "attendee_email": "attendee@test.example",
            "items": [{"ticket_type_id": v["kind"], "quantity": quantity}],
        },
    )


def test_expiry_releases_once_and_recovers_inventory_on_next_read(scenario):  # noqa: F811
    client, engine, v = scenario
    ready(engine, v, free=True)
    buyer = v["tokens"][1]
    result = checkout(client, v, buyer)
    assert result.status_code == 201, result.text
    order_id = UUID(result.json()["id"])
    with Session(engine) as db:
        db.get(Order, order_id).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    rows = client.get(f"/api/v1/events/{v['event']}/ticket-types").json()
    assert rows[0]["reserved_count"] == 0
    with Session(engine) as db:
        assert db.get(Order, order_id).status == "expired"
        assert expire_event(db, UUID(v["event"])) == 0
        db.commit()
    assert (
        client.post(
            f"/api/v1/checkout/sessions/{order_id}/complete",
            headers=buyer,
            json={"outcome": "success"},
        ).status_code
        == 409
    )


def test_abandonment_is_not_payment_failure_and_is_owner_scoped(scenario):  # noqa: F811
    client, engine, v = scenario
    ready(engine, v, free=True)
    owner, buyer, staff, stranger = v["tokens"]
    order = checkout(client, v, buyer).json()
    url = f"/api/v1/checkout/sessions/{order['id']}/cancel"
    assert client.post(url, headers=stranger).status_code == 404
    assert client.post(url, headers=buyer).json()["status"] == "cancelled"
    assert client.post(url, headers=buyer).json()["status"] == "cancelled"
    with Session(engine) as db:
        assert db.get(TicketType, UUID(v["kind"])).reserved_count == 0
        assert db.scalar(select(func.count(Payment.id))) == 0
    assert (
        client.post(
            f"/api/v1/checkout/sessions/{order['id']}/complete",
            headers=buyer,
            json={"outcome": "success"},
        ).status_code
        == 409
    )


def test_expired_completion_releases_without_background_worker(scenario):  # noqa: F811
    client, engine, v = scenario
    ready(engine, v, free=True)
    result = checkout(client, v, v["tokens"][1]).json()
    with Session(engine) as db:
        db.get(Order, UUID(result["id"])).expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
    assert (
        client.post(
            f"/api/v1/checkout/sessions/{result['id']}/complete",
            headers=v["tokens"][1],
            json={"outcome": "success"},
        ).status_code
        == 409
    )
    with Session(engine) as db:
        assert db.get(TicketType, UUID(v["kind"])).reserved_count == 0


def test_checkout_retry_does_not_reserve_twice_and_rejects_changed_payload(scenario):  # noqa: F811
    client, engine, v = scenario
    ready(engine, v, free=True)
    one = checkout(client, v, v["tokens"][1], key="checkout-retry")
    two = checkout(client, v, v["tokens"][1], key="checkout-retry")
    assert one.status_code == two.status_code == 201
    assert one.json()["id"] == two.json()["id"]
    assert checkout(client, v, v["tokens"][1], key="checkout-retry", quantity=2).status_code == 409
    with Session(engine) as db:
        assert db.get(TicketType, UUID(v["kind"])).reserved_count == 1


def test_private_invitation_email_binding_revocation_and_checkout(scenario):  # noqa: F811
    client, engine, v = scenario
    ready(engine, v, free=True, private=True)
    owner, buyer, staff, stranger = v["tokens"]
    base = f"/api/v1/events/{v['event']}"
    assert client.get(base).status_code == 403
    assert client.get(base + "/ticket-types").status_code == 403
    assert checkout(client, v, stranger).status_code == 403
    assert (
        client.post(
            base + "/invitations", headers=staff, json={"email": "attendee@test.example"}
        ).status_code
        == 403
    )
    invite = client.post(
        base + "/invitations", headers=owner, json={"email": "attendee@test.example", "days": 2}
    )
    assert invite.status_code == 201, invite.text
    code = invite.json()["token"]
    assert (
        client.post(
            "/api/v1/invitations/accept", headers=stranger, json={"token": code}
        ).status_code
        == 403
    )
    assert (
        client.post("/api/v1/invitations/accept", headers=buyer, json={"token": code}).status_code
        == 200
    )
    assert len(client.get("/api/v1/events/invited/mine", headers=buyer).json()) == 1
    result = checkout(client, v, buyer)
    assert result.status_code == 201, result.text
    client.post(base + f"/invitations/{invite.json()['id']}/revoke", headers=owner)
    assert checkout(client, v, buyer).status_code == 403
    assert (
        client.post(
            f"/api/v1/checkout/sessions/{result.json()['id']}/complete",
            headers=buyer,
            json={"outcome": "success"},
        ).status_code
        == 409
    )
    assert client.get("/api/v1/events/invited/mine", headers=buyer).json() == []
    with Session(engine) as db:
        row = db.get(EventInvitation, UUID(invite.json()["id"]))
        assert row.token_hash != code and code not in row.token_hash


def test_private_invitation_expiry_and_existing_ticket_access(scenario):  # noqa: F811
    client, engine, v = scenario
    ready(engine, v, free=True, private=True)
    owner, buyer = v["tokens"][:2]
    base = f"/api/v1/events/{v['event']}"
    invitation = client.post(
        base + "/invitations", headers=owner, json={"email": "attendee@test.example", "days": 1}
    ).json()
    with Session(engine) as db:
        db.get(EventInvitation, UUID(invitation["id"])).expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
        db.commit()
    assert (
        client.post(
            "/api/v1/invitations/accept", headers=buyer, json={"token": invitation["token"]}
        ).status_code
        == 409
    )
    # An already owned order allows historical information, but never new purchases.
    assert client.get(base, headers=buyer).status_code == 200
    assert checkout(client, v, buyer).status_code == 403


def test_venue_edit_is_isolated_and_notifies_attendees_once(scenario):  # noqa: F811
    client, engine, v = scenario
    owner, buyer, staff, stranger = v["tokens"]
    base = f"/api/v1/events/{v['event']}"
    with Session(engine) as db:
        old_id = db.get(Event, UUID(v["event"])).venue_id
    body = {"venue": {"name": "New Hall", "address": "New address 10", "city": "Astana"}}
    assert client.patch(base, headers=staff, json=body).status_code == 403
    result = client.patch(base, headers=owner, json=body)
    assert result.status_code == 200, result.text
    assert result.json()["venue"]["name"] == "New Hall"
    client.patch(base, headers=owner, json=body)
    with Session(engine) as db:
        assert db.get(Venue, old_id).name == "Hall"
        assert (
            db.scalar(
                select(func.count(Notification.id)).where(Notification.kind == "event_update")
            )
            == 1
        )
    assert (
        client.patch(base, headers=owner, json={"ends_at": "2020-01-01T00:00:00Z"}).status_code
        == 422
    )


def test_ticket_inventory_visibility_and_edit_permissions(scenario):  # noqa: F811
    client, engine, v = scenario
    owner, buyer, staff, stranger = v["tokens"]
    ready(engine, v, free=True)
    base = f"/api/v1/events/{v['event']}"
    assert client.get(base + "/inventory", headers=stranger).status_code == 403
    inventory = client.get(base + "/inventory", headers=owner).json()
    row = inventory["ticket_types"][0]
    assert row["available"] == 18 and row["sold_count"] == 2 and row["checked_in"] == 0
    assert not row["editable"]
    assert client.post(base + f"/ticket-types/{v['kind']}/hide", headers=staff).status_code == 403
    assert client.post(base + f"/ticket-types/{v['kind']}/hide", headers=owner).status_code == 200
    assert client.get(base + "/ticket-types").json() == []
    assert checkout(client, v, buyer).status_code == 404
    assert (
        client.post(base + f"/ticket-types/{v['kind']}/restore", headers=owner).status_code == 200
    )
    assert len(client.get(base + "/ticket-types").json()) == 1


def test_hiding_paid_type_preserves_review_but_not_unauthorized_price_edits(scenario):  # noqa: F811
    client, engine, v = scenario
    ready(engine, v)
    owner = v["tokens"][0]
    base = f"/api/v1/events/{v['event']}"
    assert client.post(base + f"/ticket-types/{v['kind']}/hide", headers=owner).status_code == 200
    with Session(engine) as db:
        e = db.get(Event, UUID(v["event"]))
        review = db.scalar(
            select(PaidEventApplication).where(PaidEventApplication.event_id == e.id)
        )
        assert review.status == "approved" and review.snapshot_hash == fingerprint(db, e)
    assert (
        client.post(base + f"/ticket-types/{v['kind']}/restore", headers=owner).status_code == 200
    )


def test_settings_are_admin_only_versioned_and_apply_to_new_holds(scenario):  # noqa: F811
    client, engine, v = scenario
    admin = admin_headers(engine)
    ready(engine, v)
    owner, buyer = v["tokens"][:2]
    before = checkout(client, v, buyer).json()
    data = {
        "activation_fee_minor": 250000,
        "processing_fee_bps": 500,
        "hold_minutes": 5,
        "version": 1,
        "reason": "Demo pricing change",
    }
    assert (
        client.patch("/api/v1/admin/platform-settings", headers=owner, json=data).status_code == 403
    )
    result = client.patch("/api/v1/admin/platform-settings", headers=admin, json=data)
    assert result.status_code == 200, result.text
    assert result.json()["version"] == 2
    assert (
        client.patch("/api/v1/admin/platform-settings", headers=admin, json=data).status_code == 409
    )
    after = checkout(client, v, buyer).json()
    assert before["processing_fee_minor"] == 3000
    assert after["processing_fee_minor"] == 5000
    with Session(engine) as db:
        assert db.get(Order, UUID(before["id"])).processing_fee_minor == 3000


def test_reports_have_own_history_admin_queue_and_no_automatic_suspension(scenario):  # noqa: F811
    client, engine, v = scenario
    ready(engine, v, free=True)
    admin = admin_headers(engine)
    owner, buyer, staff, stranger = v["tokens"]
    base = f"/api/v1/events/{v['event']}"
    r = client.post(
        base + "/reports", headers=buyer, json={"reason": "Event details appear misleading"}
    )
    assert r.status_code == 201, r.text
    assert (
        client.post(
            base + "/reports", headers=buyer, json={"reason": "Duplicate report"}
        ).status_code
        == 409
    )
    assert client.get("/api/v1/admin/event-reports", headers=buyer).status_code == 403
    assert client.get("/api/v1/reports/mine", headers=stranger).json() == []
    result = client.patch(
        f"/api/v1/admin/event-reports/{r.json()['id']}",
        headers=admin,
        json={"status": "resolved", "resolution": "Organizer clarified the description"},
    )
    assert result.status_code == 200, result.text
    with Session(engine) as db:
        assert db.get(Event, UUID(v["event"])).status == "published"
        assert db.scalar(select(Notification.id).where(Notification.kind == "report_status"))


def test_disputes_block_payout_and_resolution_does_not_refund_automatically(scenario):  # noqa: F811
    client, engine, v = scenario
    admin = admin_headers(engine)
    owner, buyer, staff, stranger = v["tokens"]
    with Session(engine) as db:
        e = db.get(Event, UUID(v["event"]))
        e.status = "completed"
        e.ends_at = datetime.now(UTC) - timedelta(days=1)
        o = db.get(Order, UUID(v["order"]))
        o.subtotal_minor = 200000
        o.processing_fee_minor = 6000
        o.total_minor = 206000
        db.commit()
    payout = client.get("/api/v1/organizer/payouts", headers=owner).json()[0]
    assert payout["eligible"] and payout["estimated_payout_minor"] == 200000
    path = f"/api/v1/orders/{v['order']}/disputes"
    assert client.post(path, headers=stranger, json={"reason": "Payment issue"}).status_code == 404
    disputed = client.post(
        path, headers=buyer, json={"reason": "Organizer did not resolve payment issue"}
    )
    assert disputed.status_code == 201, disputed.text
    payout = client.get("/api/v1/organizer/payouts", headers=owner).json()[0]
    assert not payout["eligible"] and payout["status"] == "blocked"
    pp = f"/api/v1/admin/payouts/{v['event']}"
    assert (
        client.patch(pp, headers=admin, json={"status": "paid", "note": "Try payout"}).status_code
        == 409
    )
    client.patch(
        f"/api/v1/admin/disputes/{disputed.json()['id']}",
        headers=admin,
        json={"status": "resolved", "resolution": "Payment records verified with attendee"},
    )
    with Session(engine) as db:
        assert db.get(Order, UUID(v["order"])).status == "confirmed"
    result = client.patch(
        pp, headers=admin, json={"status": "paid", "note": "Simulated settlement complete"}
    )
    assert result.status_code == 200, result.text
    assert result.json()["amount_minor"] == 200000
    assert (
        client.patch(
            pp, headers=admin, json={"status": "paid", "note": "Retry transfer"}
        ).status_code
        == 409
    )
    assert client.get("/api/v1/organizer/payouts", headers=staff).status_code == 403


def test_activation_fee_amount_survives_platform_setting_changes(scenario):  # noqa: F811
    client, engine, v = scenario
    owner = v["tokens"][0]
    admin = admin_headers(engine)
    url = f"/api/v1/events/{v['event']}/paid-sales/activation-fee"
    first = client.post(url, headers=owner)
    assert first.status_code == 200, first.text
    assert first.json()["amount_minor"] == 100000
    client.patch(
        "/api/v1/admin/platform-settings",
        headers=admin,
        json={
            "activation_fee_minor": 500000,
            "processing_fee_bps": 300,
            "hold_minutes": 15,
            "version": 1,
            "reason": "Update future fees",
        },
    )
    repeated = client.post(url, headers=owner)
    assert repeated.json()["amount_minor"] == 100000


def test_cyrillic_user_search_and_role_search(scenario):  # noqa: F811
    from app.users.models import User

    client, engine, v = scenario
    admin = admin_headers(engine)
    with Session(engine) as db:
        db.get(User, v["owner_id"]).full_name = "Айгерім Ұйымдастырушы"
        db.commit()
    rows = client.get("/api/v1/admin/users", headers=admin, params={"q": "айгерім"}).json()
    assert len(rows) == 1 and rows[0]["id"] == str(v["owner_id"])
    rows = client.get("/api/v1/admin/users", headers=admin, params={"q": "organizer"}).json()
    assert all("organizer" in row["roles"] for row in rows)


def test_live_checkout_hold_blocks_payout_and_refund_preserves_paid_ledger(scenario):  # noqa: F811
    client, engine, v = scenario
    admin = admin_headers(engine)
    owner = v["tokens"][0]
    with Session(engine) as db:
        e = db.get(Event, UUID(v["event"]))
        e.status = "completed"
        e.ends_at = datetime.now(UTC) - timedelta(days=1)
        original = db.get(Order, UUID(v["order"]))
        original.subtotal_minor = 200000
        original.total_minor = 206000
        original.processing_fee_minor = 6000
        pending = Order(
            event_id=e.id,
            user_id=v["buyer_id"],
            attendee_name="Buyer",
            attendee_email="attendee@test.example",
            status="pending",
            total_minor=0,
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )
        db.add(pending)
        db.flush()
        pending_id = pending.id
        db.commit()
    assert client.get("/api/v1/organizer/payouts", headers=owner).json()[0]["eligible"] is False
    with Session(engine) as db:
        db.get(Order, pending_id).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    url = f"/api/v1/admin/payouts/{v['event']}"
    assert (
        client.patch(
            url, headers=admin, json={"status": "paid", "note": "Simulated settlement"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/orders/{v['order']}/refunds",
            headers=owner,
            json={"reason": "Post-settlement demonstration refund"},
        ).status_code
        == 201
    )
    summary = client.get("/api/v1/organizer/payouts", headers=owner).json()[0]
    assert summary["recorded_paid"] is True and summary["amount_minor"] == 200000
    assert summary["estimated_payout_minor"] == 0 and summary["status"] == "blocked"
    assert (
        client.patch(
            url, headers=admin, json={"status": "paid", "note": "Cannot pay twice"}
        ).status_code
        == 409
    )


def test_hidden_sold_tickets_still_occupy_event_capacity(scenario):  # noqa: F811
    client, engine, v = scenario
    ready(engine, v, free=True)
    owner = v["tokens"][0]
    base = f"/api/v1/events/{v['event']}"
    assert client.post(base + f"/ticket-types/{v['kind']}/hide", headers=owner).status_code == 200
    now = datetime.now(UTC)
    body = dict(
        name="New admission",
        kind="free",
        price_minor=0,
        quantity=19,
        sales_start=now.isoformat(),
        sales_end=(now + timedelta(hours=1)).isoformat(),
        max_per_order=5,
    )
    assert client.post(base + "/ticket-types", headers=owner, json=body).status_code == 422
    result = client.post(base + "/ticket-types", headers=owner, json={**body, "quantity": 18})
    assert result.status_code == 201, result.text
    rows = client.get(base + "/inventory", headers=owner).json()["ticket_types"]
    assert next(r for r in rows if r["id"] == result.json()["id"])["available"] == 18
    assert (
        client.post(base + f"/ticket-types/{v['kind']}/restore", headers=owner).status_code == 422
    )


def test_event_report_support_is_private_and_routes_only_to_admins(scenario):  # noqa: F811
    client, engine, v = scenario
    ready(engine, v, free=True)
    owner, buyer, staff, stranger = v["tokens"]
    administrator = admin_headers(engine)
    payload = {
        "flow": "attendee",
        "category": "report_event",
        "event_id": v["event"],
        "subject": "Misleading event",
        "message": "Please check the advertised venue.",
    }
    result = client.post("/api/v1/support/cases", headers=buyer, json=payload)
    assert result.status_code == 201, result.text
    case = result.json()
    assert case["routing"] == "platform" and case["assigned_to"] is None
    case_path = f"/api/v1/support/cases/{case['id']}"
    for headers in [owner, staff, stranger]:
        assert client.get(case_path, headers=headers).status_code == 403
        assert client.get(case_path + "/messages", headers=headers).status_code == 403
        assert client.get(case_path + "/history", headers=headers).status_code == 403
        assert (
            client.post(
                case_path + "/messages", headers=headers, json={"body": "Unauthorized reply"}
            ).status_code
            == 403
        )
    assert case["id"] in [
        c["id"]
        for c in client.get("/api/v1/support/cases?flow=attendee&view=mine", headers=buyer).json()
    ]
    assert case["id"] not in [
        c["id"]
        for c in client.get("/api/v1/support/cases?flow=attendee&view=inbox", headers=owner).json()
    ]
    assert case["id"] in [
        c["id"]
        for c in client.get(
            "/api/v1/support/cases?flow=attendee&view=inbox", headers=administrator
        ).json()
    ]
    candidates = client.get(case_path + "/assignees", headers=administrator).json()
    assert len(candidates) == 1
    assert (
        client.patch(
            case_path + "/assignee", headers=administrator, json={"user_id": str(v["owner_id"])}
        ).status_code
        == 403
    )
    assert (
        client.post(
            case_path + "/messages",
            headers=administrator,
            json={"body": "We are reviewing this report."},
        ).status_code
        == 201
    )
    assert client.post("/api/v1/support/cases", headers=owner, json=payload).status_code == 403
    assert (
        client.post(
            "/api/v1/support/cases", headers=buyer, json={**payload, "event_id": None}
        ).status_code
        == 403
    )
    with Session(engine) as db:
        notices = list(
            db.scalars(select(Notification).where(Notification.kind == "support_request"))
        )
        assert notices and all(n.user_id != v["owner_id"] for n in notices)
