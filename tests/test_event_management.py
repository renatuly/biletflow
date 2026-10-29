from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_refund_support import scenario  # noqa: F401

from app.core.security import create_token
from app.events.models import Event
from app.orders.models import Order, OrderItem
from app.tickets.models import Ticket, TicketType
from app.users.models import Notification, User


def admin_headers(engine):
    with Session(engine) as db:
        admin = User(email="admin@ops.example", password_hash="unused", roles=["platform_admin"])
        db.add(admin)
        db.flush()
        headers = {"Authorization": f"Bearer {create_token(str(admin.id))}"}
        db.commit()
        return headers


def test_analytics_refunds_once_and_scopes_filters(scenario):  # noqa: F811
    client, engine, v = scenario
    owner, buyer, staff, stranger = v["tokens"]
    path = f"/api/v1/events/{v['event']}/analytics"
    with Session(engine) as db:
        o = db.get(Order, UUID(v["order"]))
        o.subtotal_minor = 200000
        o.discount_minor = 20000
        o.processing_fee_minor = 10000
        o.total_minor = 190000
        db.commit()
    a = client.get(path, headers=owner).json()
    assert a["gross_sales_minor"] == 200000
    assert a["discounts_minor"] == 20000
    assert a["net_revenue_minor"] == 190000
    assert a["tickets_sold"] == 2 and a["checked_in"] == 0
    assert client.get(path, headers=staff).status_code == 403
    assert client.get(path, headers=stranger).status_code == 403
    assert client.get(path, headers=buyer).status_code == 403
    assert (
        client.get(path, headers=owner, params={"date_from": "2099-01-01"}).json()[
            "net_revenue_minor"
        ]
        == 0
    )
    assert (
        client.get(
            path, headers=owner, params={"date_from": "2099-01-01", "date_to": "2020-01-01"}
        ).status_code
        == 422
    )
    client.post(
        f"/api/v1/orders/{v['order']}/refunds", headers=owner, json={"reason": "Cancelled event"}
    )
    a = client.get(path, headers=owner).json()
    assert a["refunds_minor"] == 190000 and a["net_revenue_minor"] == 0
    assert a["gross_sales_minor"] == 200000 and a["tickets_sold"] == 0
    assert a["sales_series"][0]["revenue_minor"] == 0


def test_duplicate_copies_configuration_but_not_transactions_or_staff(scenario):  # noqa: F811
    client, engine, v = scenario
    owner, buyer, staff, stranger = v["tokens"]
    path = f"/api/v1/events/{v['event']}/duplicate"
    assert client.post(path, headers=staff).status_code == 403
    response = client.post(path, headers=owner)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "draft" and result["paid_sales_status"] == "not_ready"
    assert len(result["available_ticket_types"]) == 1
    assert result["available_ticket_types"][0]["sold_count"] == 0
    with Session(engine) as db:
        copied = UUID(result["id"])
        assert db.scalar(select(func.count(Order.id)).where(Order.event_id == copied)) == 0
        assert db.scalar(select(func.count(Ticket.id)).where(Ticket.event_id == copied)) == 0
        assert db.get(Event, copied).activation_fee_paid is False


def make_free(engine, v):
    with Session(engine) as db:
        kind = db.get(TicketType, UUID(v["kind"]))
        kind.kind = "free"
        kind.price_minor = 0
        order = db.get(Order, UUID(v["order"]))
        order.total_minor = 0
        for i in db.scalars(select(OrderItem).where(OrderItem.order_id == order.id)):
            i.unit_price_minor = 0
        db.commit()


def test_free_registration_cancellation_invalidates_and_releases_once(scenario):  # noqa: F811
    client, engine, v = scenario
    owner, buyer, staff, stranger = v["tokens"]
    make_free(engine, v)
    path = f"/api/v1/orders/{v['order']}/cancel-registration"
    assert client.get(f"/api/v1/orders/{v['order']}", headers=owner).json()[
        "can_cancel_registration"
    ]
    for headers in [buyer, staff, stranger]:
        assert (
            client.post(
                path, headers=headers, json={"reason": "Requested cancellation"}
            ).status_code
            == 403
        )
    assert client.post(path, headers=owner, json={"reason": "  "}).status_code == 422
    assert (
        client.post(path, headers=owner, json={"reason": "Requested cancellation"}).status_code
        == 200
    )
    assert (
        client.post(path, headers=owner, json={"reason": "Requested cancellation"}).status_code
        == 409
    )
    with Session(engine) as db:
        assert db.get(Order, UUID(v["order"])).status == "cancelled"
        assert db.get(TicketType, UUID(v["kind"])).sold_count == 0
        assert all(t.status == "cancelled" for t in db.scalars(select(Ticket)))
        assert (
            db.scalar(
                select(func.count(Notification.id)).where(
                    Notification.kind == "registration_cancelled"
                )
            )
            == 1
        )
    for suffix in ("qr", "pdf"):
        assert (
            client.get(f"/api/v1/tickets/{v['tickets'][0]}/{suffix}", headers=buyer).status_code
            == 409
        )
    history = client.get(f"/api/v1/events/{v['event']}/activity", headers=staff).json()
    assert history[0]["action"] == "order.registration_cancelled"
    assert history[0]["entity_id"] == v["order"]
    assert client.get(f"/api/v1/events/{v['event']}/activity", headers=stranger).status_code == 403


def test_free_cancellation_rejects_paid_and_checked_in(scenario):  # noqa: F811
    client, engine, v = scenario
    owner = v["tokens"][0]
    path = f"/api/v1/orders/{v['order']}/cancel-registration"
    assert (
        client.post(path, headers=owner, json={"reason": "Cancel registration"}).status_code == 409
    )
    make_free(engine, v)
    with Session(engine) as db:
        db.get(Ticket, UUID(v["tickets"][0])).status = "checked_in"
        db.commit()
    assert (
        client.post(path, headers=owner, json={"reason": "Cancel registration"}).status_code == 409
    )


def test_admin_suspension_reversible_and_preserves_tickets(scenario):  # noqa: F811
    client, engine, v = scenario
    admin = admin_headers(engine)
    owner = v["tokens"][0]
    path = f"/api/v1/admin/events/{v['event']}/controls"
    with Session(engine) as db:
        db.get(Event, UUID(v["event"])).status = "published"
        db.commit()
    assert (
        client.post(
            path, headers=owner, json={"action": "suspend_event", "reason": "Safety review"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            path, headers=admin, json={"action": "suspend_event", "reason": "Safety review"}
        ).status_code
        == 200
    )
    assert client.post(f"/api/v1/events/{v['event']}/publish", headers=owner).status_code == 409
    assert client.post(f"/api/v1/events/{v['event']}/unpublish", headers=owner).status_code == 409
    assert (
        client.post(
            f"/api/v1/check-in/events/{v['event']}/tickets/{v['tickets'][0]}/admit", headers=owner
        ).status_code
        == 409
    )
    with Session(engine) as db:
        assert all(t.status == "valid" for t in db.scalars(select(Ticket)))
    result = client.post(
        path, headers=admin, json={"action": "restore_event", "reason": "Review completed"}
    )
    assert result.json()["status"] == "published"
    rows = client.get(
        f"/api/v1/events/{v['event']}/activity", headers=owner, params={"action": "operations"}
    ).json()
    assert len(rows) == 2


def test_paid_sales_suspension_does_not_block_existing_admission(scenario):  # noqa: F811
    client, engine, v = scenario
    admin = admin_headers(engine)
    owner = v["tokens"][0]
    path = f"/api/v1/admin/events/{v['event']}/controls"
    with Session(engine) as db:
        e = db.get(Event, UUID(v["event"]))
        e.status = "published"
        e.paid_sales_status = "active"
        db.commit()
    assert (
        client.post(
            path, headers=admin, json={"action": "suspend_sales", "reason": "Payment investigation"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/check-in/events/{v['event']}/tickets/{v['tickets'][0]}/admit", headers=owner
        ).status_code
        == 200
    )
    result = client.post(
        path, headers=admin, json={"action": "restore_sales", "reason": "Investigation completed"}
    )
    # No current approval in fixture: releasing the hold never grants paid-sales approval.
    assert result.json()["paid_sales_status"] == "not_ready"


def test_existing_checkout_cannot_finish_during_suspension(scenario):  # noqa: F811
    client, engine, v = scenario
    admin = admin_headers(engine)
    owner, buyer = v["tokens"][:2]
    make_free(engine, v)
    with Session(engine) as db:
        db.get(Event, UUID(v["event"])).status = "published"
        db.commit()
    response = client.post(
        "/api/v1/checkout/sessions",
        headers=buyer,
        json={
            "event_id": v["event"],
            "attendee_name": "Buyer",
            "attendee_email": "buyer@test.example",
            "items": [{"ticket_type_id": v["kind"], "quantity": 1}],
        },
    )
    assert response.status_code == 201, response.text
    order_id = response.json()["id"]
    controls = f"/api/v1/admin/events/{v['event']}/controls"
    client.post(
        controls, headers=admin, json={"action": "suspend_event", "reason": "Operational check"}
    )
    response = client.post(
        f"/api/v1/checkout/sessions/{order_id}/complete", headers=buyer, json={"outcome": "success"}
    )
    assert response.status_code == 409
    client.post(
        controls,
        headers=admin,
        json={"action": "restore_event", "reason": "Operational check complete"},
    )
    response = client.post(
        f"/api/v1/checkout/sessions/{order_id}/complete", headers=buyer, json={"outcome": "success"}
    )
    assert response.status_code == 200, response.text
    history = client.get(f"/api/v1/events/{v['event']}/activity", headers=owner).json()
    assert any(
        row["action"] == "order.confirmed" and row["entity_id"] == order_id for row in history
    )


def test_copied_ticket_types_edit_without_changing_source(scenario):  # noqa: F811
    client, engine, v = scenario
    owner, buyer, staff = v["tokens"][:3]
    copied = client.post(f"/api/v1/events/{v['event']}/duplicate", headers=owner).json()
    assert copied["is_duplicate"] is True
    kind = copied["available_ticket_types"][0]
    body = {
        k: kind[k]
        for k in [
            "name",
            "description",
            "kind",
            "price_minor",
            "quantity",
            "sales_start",
            "sales_end",
            "max_per_order",
        ]
    }
    body["price_minor"] = 300000
    path = f"/api/v1/events/{copied['id']}/ticket-types/{kind['id']}"
    assert client.patch(path, headers=staff, json=body).status_code == 403
    response = client.patch(path, headers=owner, json=body)
    assert response.status_code == 200, response.text
    with Session(engine) as db:
        assert db.get(TicketType, UUID(v["kind"])).price_minor == 100000
        assert db.get(TicketType, UUID(kind["id"])).price_minor == 300000
    original = f"/api/v1/events/{v['event']}/ticket-types/{v['kind']}"
    assert client.patch(original, headers=owner, json=body).status_code == 409


def test_operational_monitor_and_account_restoration_are_admin_only(scenario):  # noqa: F811
    client, engine, v = scenario
    admin = admin_headers(engine)
    owner, buyer = v["tokens"][:2]
    assert client.get("/api/v1/admin/operations/orders", headers=owner).status_code == 403
    result = client.get("/api/v1/admin/operations/orders", headers=admin).json()
    assert result["orders"][0]["id"] == v["order"] and result["is_simulation"]
    path = f"/api/v1/admin/users/{v['buyer_id']}"
    assert (
        client.post(
            path + "/restore", headers=owner, json={"reason": "Access review complete"}
        ).status_code
        == 403
    )
    assert (
        client.post(path + "/suspend", headers=admin, json={"reason": "Access review"}).status_code
        == 200
    )
    with Session(engine) as db:
        assert not db.get(User, v["buyer_id"]).is_active
    assert (
        client.post(
            path + "/restore", headers=admin, json={"reason": "Access review complete"}
        ).status_code
        == 200
    )
    with Session(engine) as db:
        assert db.get(User, v["buyer_id"]).is_active
