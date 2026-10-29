import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import email
from app.core.config import get_settings
from app.core.database import Base, get_db
from app.main import app


@pytest.fixture
def mailbox(monkeypatch):
    """Capture outgoing email in memory instead of logging or sending it."""
    monkeypatch.setattr(get_settings(), "email_backend", "memory")
    email.outbox.clear()
    yield email.outbox
    email.outbox.clear()


@pytest.fixture
def engine():
    # Set TEST_DATABASE_URL to an empty PostgreSQL database to run the suite against it.
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        engine = create_engine(url)
        Base.metadata.drop_all(engine)
    else:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def client(engine, mailbox):
    def database():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = database
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
