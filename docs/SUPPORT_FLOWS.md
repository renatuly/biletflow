# Support flows

Support is REST-based with 30-second idle polling. Notifications are persisted in
`notifications` and displayed in-app. This feature does not send email or claim email delivery.
Payments and refunds remain simulated.

## Queues and authorization

`flow=attendee` contains customer conversations. With an event, the event organizer
and active `StaffAssignment` members explicitly granted `support` can handle them.
Scanning, reverse-check-in and manage-attendee permissions do not grant support.
Without an event, attendee cases fall back to Platform Admins. Assignment never widens
access; revoked staff lose access immediately even if still named as assignee.

`flow=organizer_platform` contains organizer-to-Platform-Admin conversations. Only
organizers can create them; only Platform Admins handle/assign them. Event/order/ticket
context must belong to that organizer's event. Other organizers cannot read the case.
Categories are account, activation, payment and technical. Attendee categories are
ticket_delivery, payment, refund, seating, event_information, check_in, account and technical.

Requesters can read only their own cases unless they separately have staff authority.
The client always sends explicit flow and `view=mine|inbox`; multi-role accounts can
choose their requester context separately from their handling queue. The default
`view=all` is retained for older API clients and still enforces every access check.

## API

All paths below are relative to `/api/v1` and require authentication.

- GET /support/capabilities: current server capabilities.
- GET /support/cases?flow=attendee|organizer_platform&view=mine|inbox|all
- POST /support/cases: flow, category, subject, message, optional event_id/order_id/ticket_id.
- GET /support/cases/{id}: case number, flow/routing, assignee, linked record summaries,
  independent order/ticket state and server-derived can_manage/can_refund flags.
- GET/POST /support/cases/{id}/messages: chronological messages with sender name/time.
- GET /support/cases/{id}/assignees: authorized active assignees, handlers only.
- PATCH /support/cases/{id}/assignee: {user_id: UUID or null}, handlers only.
- PATCH /support/cases/{id}/status?status=open|in_progress|waiting_for_customer|resolved
- GET /support/cases/{id}/history: immutable creation, assignment and status changes.
- POST /support/cases/{id}/refund: separate full-order refund; organizer owner/admin only.
- GET/POST /support/events/{event_id}/staff: list explicit grants or grant by {email}.
- DELETE /support/events/{event_id}/staff/{user_id}: revoke support, retain other permissions.
  Only the event owner or Platform Admin can manage these grants.

Context is derived from owned tickets/orders. Mismatched identifiers are rejected.
Unpublished event context requires a purchased order for attendee requests. A refund
conversation may ask a general policy question without a purchase; actual refunds
require a linked confirmed paid order. Linked summaries are returned only after scope
and relationship checks; no ticket credentials are included in support responses.

The mobile inbox filters event, status, category and assignee within the authorized
result set. Reading a linked ID elsewhere continues to use that endpoint's permissions;
case handlers see relevant scoped summaries in the conversation rather than receiving
unrestricted order/ticket API access.

## State, history and retry rules

New cases start Open. Only authorized handlers assign or change status. A requester
reply to Waiting for Customer moves it to In Progress and records a history event.
Resolved conversations are read-only until a handler explicitly reopens them; customers
can create another case. Completed-refund cases cannot be reopened. History is retained.

Resolving any case, including a refund case, NEVER refunds an order. Refund execution
is a separate owner/admin operation. It changes order/ticket state and resolves linked
refund cases, with history and the existing refund notification. A resolved conversation
for an otherwise refundable order can still be refunded through that separate action.
Support-only agents cannot issue refunds.

Send an Idempotency-Key (1–128 characters) for POST cases/messages. Uniqueness is
persisted per requester/case and message sender. Repeating the same intent returns the
original result; reusing a key for different content returns 409. The Flutter service
retains keys after uncertain failures and uses a new key after acknowledged success.
Clients omitting keys retain legacy behavior and do not get retry deduplication.

New replies, assignments and status changes notify the requester and eligible assigned
handler; unassigned platform cases notify active Platform Admins. Notifications include
case_id and flow so the app can open the authorized conversation. Revoked assignees
are excluded. No WebSockets, attachments, presence or typing indicators are used.

## Persistence upgrade

This project uses create_schema() bootstrap rather than Alembic. An additive,
idempotent migration in app/support/migrate.py runs after metadata creation and can
also be run with `python -m app.support.migrate`. Back up the database before a
production rollout. It adds flow, submission-key columns/indexes and support_changes;
it does not delete or replace existing cases/messages. Existing organizer-created
account/own-event support cases are classified as organizer_platform; other historical
cases remain attendee. New change history begins at upgrade, without inventing history.

Migration is covered against a legacy SQLite schema. DDL is compatible with the
project's SQLite/PostgreSQL databases; no live PostgreSQL instance was available in
this workspace. Use one migration/startup process during deployment.
