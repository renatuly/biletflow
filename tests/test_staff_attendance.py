from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_qr_credential, create_token
from app.events.models import Event, StaffAssignment, Venue
from app.main import app
from app.orders.models import Order, OrderItem
from app.tickets.models import Ticket, TicketType
from app.users.models import User


def test_owner_and_assigned_staff_can_read_orders_but_others_cannot():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner = User(email="owner@example.com", password_hash="unused", roles=["organizer"])
        staff = User(email="staff@example.com", password_hash="unused", roles=["event_admin"])
        stranger = User(email="stranger@example.com", password_hash="unused", roles=["event_admin"])
        venue = Venue(name="Hall", address="Abay 1", city="Almaty")
        db.add_all([owner, staff, stranger, venue])
        db.flush()
        start = datetime.now(UTC) + timedelta(days=2)
        event = Event(
            organizer_id=owner.id,
            venue_id=venue.id,
            title="Event",
            description="Test",
            category="music",
            starts_at=start,
            ends_at=start + timedelta(hours=2),
            registration_opens_at=start - timedelta(days=1),
            registration_closes_at=start,
            capacity=20,
        )
        db.add(event)
        db.flush()
        db.add(
            StaffAssignment(
                event_id=event.id,
                user_id=staff.id,
                permissions=["check_in", "reverse_check_in", "manage_attendees"],
            )
        )
        order = Order(
            event_id=event.id,
            user_id=None,
            attendee_name="Test Guest",
            attendee_email="guest@example.com",
            status="confirmed",
            expires_at=start,
        )
        db.add(order)
        db.flush()
        kind = TicketType(
            event_id=event.id,
            name="General",
            kind="free",
            quantity=10,
            sales_start=start - timedelta(days=1),
            sales_end=start,
        )
        db.add(kind)
        db.flush()
        item = OrderItem(order_id=order.id, ticket_type_id=kind.id, quantity=2, unit_price_minor=0)
        db.add(item)
        db.flush()
        from uuid import uuid4

        ticket_id = uuid4()
        ticket = Ticket(
            id=ticket_id,
            order_item_id=item.id,
            event_id=event.id,
            ticket_type_id=kind.id,
            owner_id=None,
            attendee_name="Test Guest",
            attendee_email="guest@example.com",
            status="valid",
            qr_credential=create_qr_credential(str(ticket_id)),
        )
        db.add(ticket)
        attendance_url = f"/api/v1/check-in/events/{event.id}/attendees"
        admit_url = f"/api/v1/check-in/events/{event.id}/tickets/{ticket_id}/admit"
        url = f"/api/v1/events/{event.id}/orders"
        tokens = [
            {"Authorization": f"Bearer {create_token(str(user.id))}"}
            for user in [owner, staff, stranger]
        ]
        db.commit()

    def database():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = database
    try:
        with TestClient(app) as client:
            for headers in tokens[:2]:
                result = client.get(url, headers=headers)
                assert result.status_code == 200, result.text
                assert result.json()[0]["attendee_name"] == "Test Guest"
                assert "password_hash" not in result.text
            assert client.get(url, headers=tokens[2]).status_code == 403
            assert client.get(url).status_code == 401
            assert client.get(attendance_url).status_code == 401
            assert client.get(attendance_url, headers=tokens[2]).status_code == 403
            assert client.post(admit_url, headers=tokens[2]).status_code == 403
            for headers in tokens[:2]:
                roster = client.get(attendance_url, headers=headers)
                assert roster.status_code == 200, roster.text
                assert roster.json()[0]["status"] == "valid"
                assert "qr_credential" not in roster.text
                admission = client.post(admit_url, headers=headers)
                assert admission.status_code == 200, admission.text
                assert client.post(admit_url, headers=headers).status_code == 409
                row = client.get(attendance_url, headers=headers).json()[0]
                assert row["status"] == "checked_in"
                record = row["record_id"]
                reverse = f"/api/v1/check-in/records/{record}/reverse"
                assert client.post(reverse, headers=tokens[2]).status_code == 403
                assert client.post(reverse, headers=headers).status_code == 200
                assert client.post(reverse, headers=headers).status_code == 409
                assert client.get(attendance_url, headers=headers).json()[0]["status"] == "valid"
            with Session(engine) as db:
                ticket = db.get(Ticket, ticket_id)
                ticket.status = "refunded"
                db.commit()
            assert client.post(admit_url, headers=tokens[1]).status_code == 409

    finally:
        app.dependency_overrides.clear()
        engine.dispose()
