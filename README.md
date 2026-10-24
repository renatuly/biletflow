# BiletFlow

BiletFlow FastAPI backend for the Kazakhstan-focused academic MVP. It provides
accounts and roles, organizer profiles, events, ticket inventory, simulated KZT
checkout, QR/PDF tickets, online check-in, refunds, campaigns, support cases,
notifications, audit history, analytics, moderation, and calendar export.

## Run locally

Requires Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Copy `.env.example` to `.env` before changing local settings. The API will be
available at `http://127.0.0.1:8000`, with interactive
documentation at `/docs`.

To create an idempotent local dataset with attendee, organizer, check-in staff,
and administrator accounts, multiple event categories and venues, ticket types,
registrations, revenue, and attendee ticket history, run:

```bash
python -m app.dev_seed
```

All demo accounts use `DemoPass123!`; their email addresses are printed by the
command. The seed refuses to run when `APP_ENV=production`.

For PostgreSQL-based demonstration deployment:

```bash
docker compose up --build
```

## Test

```bash
pytest
```

## Current scope

All financial and organizer-verification operations are explicitly simulated for
the academic MVP. No card data is accepted or stored. The full client-facing API
design is documented in `docs/API_CONTRACT.md`.


## Organizer applications

Authenticated users without organizer/admin access can submit organizer details.
Application status is private to its applicant; only platform administrators can
list pending applications and approve them. All paths use the `/api/v1` prefix:

- `GET /organizer-applications/me`: the current application or JSON null.
- `POST /organizer-applications`: contact_name (2–200), organization_name (2–200),
  contact_phone (5–40), description (10–2000 characters); whitespace is trimmed.
- `GET /admin/organizer-applications`: pending applications (admin only).
- `POST /admin/organizer-applications/{id}/approve`: grants organizer access and
  creates the organizer profile without overwriting existing roles/profiles.

Approval sends an in-app notification and records an audit entry. Repeating an
approval returns the approved application without duplicate side effects. Paid
sales, identity verification and payouts still require their existing checks.
The mobile application refreshes `/users/me` after approval to open the dashboard.

Restart the server after installing these routes. Startup creates the new
`organizer_applications` table through the existing additive `create_schema()`
mechanism; existing users, tickets and events are preserved.
