from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import demo_accounts
from app.core.database import Base
from app.core.security import verify_password
from app.events.models import OrganizerProfile
from app.users.models import User


def test_demo_accounts_are_idempotent_and_keep_passwords(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(demo_accounts, "SessionLocal", sessionmaker(engine))
    monkeypatch.setattr(demo_accounts, "create_schema", lambda: None)

    first = demo_accounts.ensure_demo_accounts("first-password-123")
    assert all(line.endswith("created") for line in first)
    second = demo_accounts.ensure_demo_accounts("second-password-123")
    assert all(line.endswith("already present") for line in second)
    with Session(engine) as db:
        users = {u.email: u for u in db.scalars(select(User))}
        assert len(users) == 4
        admin = users["admin.demo@example.com"]
        assert "platform_admin" in admin.roles and admin.email_verified
        assert verify_password("first-password-123", admin.password_hash)
        assert "event_admin" in users["staff.demo@example.com"].roles
        assert db.scalar(select(OrganizerProfile)).user_id == users["organizer.demo@example.com"].id

    demo_accounts.ensure_demo_accounts("second-password-123", reset_passwords=True)
    with Session(engine) as db:
        admin = db.scalar(select(User).where(User.email == "admin.demo@example.com"))
        assert verify_password("second-password-123", admin.password_hash)
        assert len(db.scalars(select(OrganizerProfile)).all()) == 1
