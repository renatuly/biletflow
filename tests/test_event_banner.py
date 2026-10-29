from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_token
from app.events.models import Event, Venue
from app.main import app
from app.users.models import User


def test_banner_attachment_authorization_validation_and_retry():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner = User(
            email="owner@example.com",
            full_name="Owner",
            password_hash="unused",
            roles=["organizer"],
        )
        other = User(
            email="other@example.com", full_name="Other", password_hash="unused", roles=["attendee"]
        )
        venue = Venue(name="Venue", address="Address", city="Almaty")
        db.add_all([owner, other, venue])
        db.flush()
        start = datetime.now(UTC) + timedelta(days=7)
        event = Event(
            organizer_id=owner.id,
            venue_id=venue.id,
            title="Banner test",
            description="A test event",
            category="tech",
            starts_at=start,
            ends_at=start + timedelta(hours=2),
            registration_opens_at=start - timedelta(days=1),
            registration_closes_at=start,
            capacity=10,
        )
        db.add(event)
        db.flush()
        owner_token = {"Authorization": f"Bearer {create_token(str(owner.id))}"}
        other_token = {"Authorization": f"Bearer {create_token(str(other.id))}"}
        event_id = event.id
        db.commit()

    def database():
        with Session(engine, expire_on_commit=False) as db:
            yield db

    app.dependency_overrides[get_db] = database
    try:
        with TestClient(app) as client:
            path = f"/api/v1/events/{event_id}/images"
            body = {"url": "https://example.com/banner.jpg"}
            assert client.post(path, json=body).status_code == 401
            assert client.post(path, json=body, headers=other_token).status_code == 403
            assert (
                client.post(
                    path, json={"url": "file:///etc/passwd"}, headers=owner_token
                ).status_code
                == 422
            )
            result = client.post(path, json=body, headers=owner_token)
            assert result.status_code == 200, result.text
            assert result.json()["images"] == [body["url"]]
            assert client.post(path, json=body, headers=owner_token).json()["images"] == [
                body["url"]
            ]
            next_image = "https://example.com/updated.jpg"
            result = client.post(path, json={"url": next_image}, headers=owner_token)
            assert result.json()["images"][0] == next_image
            with Session(engine) as db:
                item = db.get(Event, event_id)
                item.status = "published"
                db.commit()
            assert client.post(path, json=body, headers=owner_token).status_code == 409
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
