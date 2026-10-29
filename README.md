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

Compose also starts [Mailpit](https://mailpit.axllent.org/); every email the API sends
(verification, password reset, ticket confirmations with PDF tickets, refunds,
cancellations) appears at `http://localhost:8025`.

## Email

`EMAIL_BACKEND` selects delivery:

- `console` (default): emails are printed to the API log. Verification and reset tokens
  are also returned in `X-Demo-Verification-Token` / `X-Demo-Reset-Token` headers.
- `smtp`: delivery through `SMTP_HOST`/`SMTP_PORT` with `SMTP_SECURITY` set to
  `starttls` (587), `ssl` (465) or `none` (local relays). Required in production.

Links in emails point to `APP_FRONTEND_URL`. Email is sent after the response with
FastAPI background tasks, so a slow or failing SMTP server never blocks checkout;
delivery errors are logged.

## Schema changes

Tables are created on startup. Columns added later are appended automatically when
they are nullable or have a server default (see `add_missing_columns`). Other schema
changes need a migration or a fresh database.

## Test

```bash
pytest
# Optionally against an empty PostgreSQL database:
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/biletflow_test pytest
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


## Completed inventory and operational workflows

Startup creates additive tables for private invitations, event reports, basic
order disputes, persisted platform settings, simulated payout records, activation
fee records and checkout retry keys. Existing records are retained. Restart the API
with ASGI lifespan enabled to run the reservation worker every 30 seconds.
Inventory reads/new checkout also expire old holds on demand. Explicit abandonment
uses `POST /checkout/sessions/{id}/cancel` and is not a failed payment.

Checkout uses event → order → ticket-type locks, rejects duplicate ticket-type
entries, enforces event capacity, and accepts `Idempotency-Key` for retry-safe
reservation creation. Private invitations are email-bound, hashed at rest,
revocable and expiring. Access is checked on event/ticket reads, checkout creation
and completion; a private hold cannot outlive its access grant. Owned historical
orders/tickets retain read access when an invitation is revoked. No invitation
email is sent automatically.

Additional routes (all below `/api/v1`):

- `GET /events/{id}/inventory`; `POST .../ticket-types/{type}/restore`.
  Existing ticket-type PATCH supports eligible unsold draft types. Hide/restore
  retains issued admissions and preserves a current review for visibility-only
  changes. Financial/detail changes still require paid-event resubmission.
- Event PATCH accepts a validated venue object, creates a distinct venue record
  on change, and notifies confirmed attendees when details actually change.
- `GET/POST /events/{id}/invitations`, `POST .../{invitation}/revoke`,
  `POST /invitations/accept`, `GET /events/invited/mine`.
- `POST /events/{id}/reports`, `GET /reports/mine`,
  `GET /admin/event-reports`, `PATCH /admin/event-reports/{id}`.
- `POST /orders/{id}/disputes`, `GET /disputes/mine`,
  `GET /admin/disputes`, `PATCH /admin/disputes/{id}`.
- `GET/PATCH /admin/platform-settings`: activation fee (minor KZT), processing
  fee (basis points), hold duration (minutes), version and audit reason. A stale
  version returns 409. Changes apply to future reservations/fee quotes only;
  existing order amounts and recorded activation fees are preserved.
- `GET /organizer/payouts`, `GET /events/{id}/financial-summary`,
  `PATCH /admin/payouts/{id}`. Payout eligibility requires event end, positive
  retained payments, no pending checkout/open dispute and no event suspension.
  Estimated payout = retained confirmed payments minus included processing fees;
  fees are not deducted twice. All payouts are simulated and a recorded transfer
  cannot be issued again. Later refunds preserve the ledger and require review.

Moderation/dispute decisions notify requesters but never implicitly refund an
order or suspend an event. Those actions use existing explicit admin/organizer
controls. All changes are scoped and audited. Run `pytest` for role, lifecycle,
expiration, access, settings, notification and payout integration coverage.


### Event reports through support

The mobile attendee event details action creates an ordinary support case with
`flow=attendee`, `category=report_event`, and the event ID. These cases route only
to platform admins even though an event is attached: organizers and event staff
cannot read, reply, assign, or receive notifications for them. An attendee role
and accessible event context are required. The mobile app uses Support Chat and
the admin platform inbox instead of separate report/dispute pages. Existing
formal report/dispute records and APIs are retained for compatibility.
