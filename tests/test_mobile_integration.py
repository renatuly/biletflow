from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.users.models import User


def test_mobile_purchase_and_checkin():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    def database():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = database
    try:
        with TestClient(app) as client:

            def account(email, role=None):
                response = client.post(
                    "/api/v1/auth/register",
                    json={
                        "email": email,
                        "password": "test-password-123",
                        "full_name": "Mobile User",
                    },
                )
                assert response.status_code == 201, response.text
                verified = client.post(
                    "/api/v1/auth/verify-email",
                    json={"token": response.headers["X-Demo-Verification-Token"]},
                )
                assert verified.json()["email_verified"] is True
                if role:
                    with Session(engine) as db:
                        user = db.scalar(select(User).where(User.email == email))
                        user.roles = [role]
                        db.commit()
                response = client.post(
                    "/api/v1/auth/login",
                    json={
                        "email": email,
                        "password": "test-password-123",
                    },
                )
                assert response.status_code == 200
                return {"Authorization": f"Bearer {response.json()['access_token']}"}

            organizer = account("organizer@example.com", "organizer")
            attendee = account("attendee@example.com")
            stranger = account("stranger@example.com")
            admin = account("admin@example.com", "platform_admin")
            now = datetime.now(UTC)
            start = now + timedelta(days=2)
            event = client.post(
                "/api/v1/events",
                headers=organizer,
                json={
                    "title": "Mobile integration event",
                    "description": "Integration test event",
                    "category": "music",
                    "venue": {"name": "Hall", "address": "123 Street", "city": "Almaty"},
                    "starts_at": start.isoformat(),
                    "ends_at": (start + timedelta(hours=2)).isoformat(),
                    "registration_opens_at": (now - timedelta(hours=1)).isoformat(),
                    "registration_closes_at": start.isoformat(),
                    "capacity": 10,
                },
            )
            assert event.status_code == 201, event.text
            event_id = event.json()["id"]
            ticket_type = client.post(
                f"/api/v1/events/{event_id}/ticket-types",
                headers=organizer,
                json={
                    "name": "General",
                    "kind": "free",
                    "price_minor": 0,
                    "quantity": 10,
                    "sales_start": (now - timedelta(hours=1)).isoformat(),
                    "sales_end": start.isoformat(),
                },
            )
            assert ticket_type.status_code == 201, ticket_type.text
            client.post(f"/api/v1/events/{event_id}/publish", headers=organizer)
            checkin_events = client.get("/api/v1/check-in/events", headers=organizer)
            assert "password_hash" not in checkin_events.text
            visible = client.get("/api/v1/events").json()[0]
            assert visible["venue"]["city"] == "Almaty"
            assert visible["organizer_name"] == "Mobile User"
            assert visible["available_ticket_types"][0]["quantity"] == 10
            order = client.post(
                "/api/v1/checkout/sessions",
                headers=attendee,
                json={
                    "event_id": event_id,
                    "attendee_name": "Mobile User",
                    "attendee_email": "attendee@example.com",
                    "items": [{"ticket_type_id": ticket_type.json()["id"], "quantity": 1}],
                },
            )
            assert order.status_code == 201, order.text
            completed = client.post(
                f"/api/v1/checkout/sessions/{order.json()['id']}/complete",
                headers=attendee,
                json={"outcome": "success"},
            )
            assert completed.json()["status"] == "confirmed", completed.text
            ticket = client.get("/api/v1/tickets", headers=attendee).json()[0]
            assert ticket["qr_credential"].startswith("ticket.")
            ticket_id = ticket["id"]
            assert client.get(f"/api/v1/tickets/{ticket_id}", headers=attendee).status_code == 200
            assert (
                client.get(f"/api/v1/tickets/{ticket_id}/qr", headers=attendee).headers[
                    "content-type"
                ]
                == "image/png"
            )
            assert (
                client.get(f"/api/v1/tickets/{ticket_id}/event", headers=stranger).status_code
                == 403
            )
            client.post(f"/api/v1/events/{event_id}/unpublish", headers=organizer)
            assert (
                client.get(f"/api/v1/tickets/{ticket_id}/event", headers=attendee).status_code
                == 200
            )
            params = {"qr_payload": ticket["qr_credential"]}
            assert (
                client.post(
                    f"/api/v1/check-in/events/{event_id}/admit", headers=organizer, params=params
                ).status_code
                == 200
            )
            assert (
                client.post(
                    f"/api/v1/check-in/events/{event_id}/admit", headers=organizer, params=params
                ).status_code
                == 409
            )
            users = client.get("/api/v1/admin/users", headers=admin).json()
            assert all("password_hash" not in user for user in users)
            assert (
                client.get("/api/v1/admin/events", headers=admin).json()[0]["venue"]["name"]
                == "Hall"
            )
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
