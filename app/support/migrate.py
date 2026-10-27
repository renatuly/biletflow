"""Idempotent additive support migration for the existing SQLite/Postgres bootstrap."""

from sqlalchemy import inspect, text


def upgrade(engine):
    inspector = inspect(engine)
    if "support_cases" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("support_cases")}
    first_upgrade = "flow" not in columns
    with engine.begin() as connection:
        for name, ddl in {
            "flow": "VARCHAR(30) NOT NULL DEFAULT 'attendee'",
            "client_key": "VARCHAR(128)",
            "request_fingerprint": "VARCHAR(64)",
        }.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE support_cases ADD COLUMN {name} {ddl}"))
        message_columns = {c["name"] for c in inspect(connection).get_columns("support_messages")}
        if "client_key" not in message_columns:
            connection.execute(
                text("ALTER TABLE support_messages ADD COLUMN client_key VARCHAR(128)")
            )
    from app.support.models import SupportCase, SupportMessage

    for table in [SupportCase.__table__, SupportMessage.__table__]:
        for index in table.indexes:
            index.create(engine, checkfirst=True)
    if first_upgrade:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from app.events.models import Event
        from app.users.models import User

        with Session(engine) as db:
            for case in db.scalars(select(SupportCase)):
                requester = db.get(User, case.requester_id)
                event = db.get(Event, case.event_id) if case.event_id else None
                if (
                    requester
                    and "organizer" in requester.roles
                    and (not event or event.organizer_id == requester.id)
                ):
                    case.flow = "organizer_platform"
                    case.assigned_to = None
                    if case.category not in {"account", "payment", "activation", "technical"}:
                        case.category = "technical"
            db.commit()


if __name__ == "__main__":
    from app import models  # noqa: F401
    from app.core.database import create_schema

    create_schema()
