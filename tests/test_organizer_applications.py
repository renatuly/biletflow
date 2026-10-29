import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_token
from app.events.models import OrganizerProfile
from app.main import app
from app.users.models import Notification, User


@pytest.fixture
def setup():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    identities = {}
    with Session(engine) as db:
        for name, roles in [
            ("attendee", ["attendee", "event_admin"]),
            ("other", ["attendee"]),
            ("admin", ["platform_admin"]),
        ]:
            user = User(
                email=f"{name}@example.com", full_name=name, password_hash="unused", roles=roles
            )
            db.add(user)
            db.flush()
            identities[name] = {"Authorization": f"Bearer {create_token(str(user.id))}"}
        db.commit()

    def database():
        with Session(engine, expire_on_commit=False) as db:
            yield db

    app.dependency_overrides[get_db] = database
    try:
        with TestClient(app) as client:
            yield client, identities, engine
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


DETAILS = {
    "contact_name": " Test Person ",
    "organization_name": " Test Events ",
    "contact_phone": "+77001234567",
    "description": "We organize community events.",
}


def test_application_approval_flow_preserves_roles_and_is_idempotent(setup):
    client, headers, engine = setup
    root = "/api/v1"
    assert (
        client.get(root + "/organizer-applications/me", headers=headers["attendee"]).json() is None
    )
    result = client.post(
        root + "/organizer-applications", headers=headers["attendee"], json=DETAILS
    )
    assert result.status_code == 201, result.text
    application = result.json()
    assert application["contact_name"] == "Test Person"
    assert application["status"] == "pending"
    assert client.get(root + "/organizer-applications/me", headers=headers["other"]).json() is None
    assert (
        "organizer"
        not in client.get(root + "/users/me", headers=headers["attendee"]).json()["roles"]
    )
    assert (
        client.post(
            root + "/organizer-applications", headers=headers["attendee"], json=DETAILS
        ).status_code
        == 409
    )
    assert (
        len(client.get(root + "/admin/organizer-applications", headers=headers["admin"]).json())
        == 1
    )
    url = root + f"/admin/organizer-applications/{application['id']}/approve"
    assert client.post(url, headers=headers["other"]).status_code == 403
    assert client.post(url, headers=headers["admin"]).json()["status"] == "approved"
    assert client.post(url, headers=headers["admin"]).status_code == 200
    profile = client.get(root + "/users/me", headers=headers["attendee"]).json()
    assert set(profile["roles"]) == {"attendee", "event_admin", "organizer"}
    assert client.get(root + "/admin/organizer-applications", headers=headers["admin"]).json() == []
    with Session(engine) as db:
        profiles = db.scalars(select(OrganizerProfile)).all()
        assert len(profiles) == 1
        assert profiles[0].verification_status == "not_started"
        assert profiles[0].payout_verified is False
        assert len(db.scalars(select(Notification)).all()) == 1


def test_application_authorization_and_validation(setup):
    client, headers, _ = setup
    root = "/api/v1"
    for path in ["/organizer-applications/me", "/admin/organizer-applications"]:
        assert client.get(root + path).status_code == 401
    assert client.post(root + "/organizer-applications", json=DETAILS).status_code == 401
    assert (
        client.get(root + "/admin/organizer-applications", headers=headers["attendee"]).status_code
        == 403
    )
    assert (
        client.post(
            root + "/organizer-applications", headers=headers["admin"], json=DETAILS
        ).status_code
        == 409
    )
    for field in DETAILS:
        result = client.post(
            root + "/organizer-applications",
            headers=headers["attendee"],
            json={**DETAILS, field: " "},
        )
        assert result.status_code == 422
    assert (
        client.post(
            root + "/admin/organizer-applications/00000000-0000-0000-0000-000000000000/approve",
            headers=headers["admin"],
        ).status_code
        == 404
    )


def test_inactive_applicant_cannot_be_approved(setup):
    client, headers, engine = setup
    result = client.post(
        "/api/v1/organizer-applications", headers=headers["attendee"], json=DETAILS
    )
    with Session(engine) as db:
        user = db.scalar(select(User).where(User.email == "attendee@example.com"))
        user.is_active = False
        db.commit()
    response = client.post(
        f"/api/v1/admin/organizer-applications/{result.json()['id']}/approve",
        headers=headers["admin"],
    )
    assert response.status_code == 409
