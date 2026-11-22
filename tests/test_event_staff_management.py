from uuid import UUID

from factories import API, account, event
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.events.models import StaffAssignment


def test_checkin_staff_assignment_merges_lists_and_removes(client, engine):
    organizer = account(client, engine, "owner@example.com", role="organizer")
    staff = account(client, engine, "staff@example.com")
    account(client, engine, "helper@example.com")
    event_id = event(client, organizer)
    base = f"{API}/events/{event_id}/staff"

    missing = client.post(base, headers=organizer, params={"email": "nobody@example.com"})
    assert missing.status_code == 404
    # Support access first, then check-in: both permissions share one assignment row.
    granted = client.post(
        f"{API}/support/events/{event_id}/staff",
        headers=organizer,
        json={"email": "staff@example.com"},
    )
    assert granted.status_code == 201, granted.text
    for _ in range(2):
        result = client.post(base, headers=organizer, params={"email": " Staff@Example.com "})
        assert result.status_code == 201, result.text
    assert {"support", "check_in"} <= set(result.json()["permissions"])
    with Session(engine) as db:
        rows = db.scalars(
            select(StaffAssignment).where(StaffAssignment.event_id == UUID(event_id))
        ).all()
        assert len(rows) == 1

    listed = client.get(base, headers=organizer).json()
    assert [member["email"] for member in listed] == ["staff@example.com"]
    # Staff can scan but cannot grant access to others or read the staff list.
    assert client.get(base, headers=staff).status_code == 403
    assert (
        client.post(base, headers=staff, params={"email": "helper@example.com"}).status_code == 403
    )

    user_id = listed[0]["user_id"]
    assert client.delete(f"{base}/{user_id}", headers=organizer).status_code == 200
    assert client.get(base, headers=organizer).json() == []
    assert client.delete(f"{base}/{user_id}", headers=organizer).status_code == 404
    # Removing check-in access keeps the separately granted support access.
    support = client.get(f"{API}/support/events/{event_id}/staff", headers=organizer).json()
    assert [member["id"] for member in support] == [user_id]
