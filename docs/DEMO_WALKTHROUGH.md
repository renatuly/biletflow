# Fresh demo walkthrough

From the backend folder, stop the local API and run:

```sh
.venv/bin/python -m app.dev_reset_seed --replace-local
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The reset builds and checks a separate database, backs up the configured local
SQLite database, and replaces all existing records with fresh IDs. It works only
with `APP_ENV=development` and a SQLite file. It does not reset hosted databases.
The target database must already exist; run the API once to initialize a new one.
Legacy additive seed commands are not needed for this dataset.

All accounts use **demo1234**. Sign out and sign back in after resetting;
previous sessions refer to deleted users. The web and mobile clients must point to
this same local backend to see the same demo. Every date is relative to reset time.
The live event lasts another three hours; the pending checkout lasts 15 minutes.
Rerun the command before a later demo to refresh those time-sensitive scenarios.
All payments, organizer checks and payouts are simulated. Seed email is captured
in process memory and discarded; no messages are delivered to real mailboxes.
Notifications and support messages persist in the database.

## Accounts

| Account | Demo starting point |
|---|---|
| attendee@example.com | Amina: bookings, refunds, cancelled tickets, ticket history, messages, reports and disputes |
| organizer@example.com | Daniyar: event catalogue, analytics, staff, support, campaigns, reviews and payouts |
| staff@example.com | Miras: check-in only, assigned events and attendee rosters |
| support@example.com | Dana: assigned attendee conversations; no financial or check-in permissions |
| admin@example.com | Aliya: reviews, moderation, platform inbox, users, settings and simulated payouts |
| city@example.com | A separate organizer for ownership and permission boundaries |
| new@example.com | Empty attendee history for a fresh browsing and booking journey |
| applicant@example.com | Pending organizer application ready for admin approval |
| rejected@example.com | Rejected application for the correction/resubmission journey |
| unverified@example.com | Email verification pending |
| suspended@example.com | Suspended account for the admin restore journey |

There are also 24 synthetic guest accounts at `guest01@example.com`
through `guest24@example.com`, with the same password.

## Attendee demonstration

1. Sign in as Amina and browse all eight categories across Almaty, Astana and
   Shymkent. Open **Aurora Live: Music Under the Stars**. It has paid General,
   Backstage / VIP and free Community guest types. **Clay and Coffee** is sold out;
   **Green Campus** has registration scheduled to open in two days.
2. Select tickets and apply **AURORA20** for 20% off. Complete simulated checkout,
   open the QR ticket, download the PDF and try the ticket-email request with the
   running backend's configured email provider. The seed does not populate a real
   mailbox. Existing Amina orders include a group order with two General and one VIP.
3. Open order history: confirmed, refunded, cancelled, failed, expired and pending
   orders are present. The pending hold naturally expires. At **Campus Connect**,
   a duplicate free registration has been cancelled while the original stays valid.
4. Open support: ticket delivery is in progress, accessible entrance is waiting
   for a reply, the PDF question is resolved, and a running-event refund awaits
   organizer review. A completed Aurora refund has already invalidated its ticket.
5. Use the private invitation code from `demo_seed_manifest.json` while signed in
   as Amina, accept **Inner Circle**, then book. Another guest already has an
   accepted invitation and booking; revoked and expired invitations appear for the
   organizer. The manifest also includes the unlisted Product Clinic event ID.
6. Open reports, disputes and notifications. Report-event support is visible to
   platform admins. Formal event reports and payment disputes have separate histories.
7. Use the new attendee for an empty-history journey, or register a new address to
   demonstrate registration, verification and password reset using the running
   backend's email setup.

## Organizer demonstration

1. Open Daniyar's dashboard and **Aurora Live** workspace: sales over 21 days,
   inventory, registrations, ticket types, processing fees, campaign attribution,
   refunds, order search and audit history are populated. **Build Tomorrow** also
   has an order containing both a paid and a free ticket.
2. Campaigns include **AURORA20** (active with redemptions), **FIRSTLIGHT** (one-use
   fixed 1,500 KZT discount, exhausted), **PAUSED15** (disabled), **LASTWEEK**
   (expired), and **NEXTWAVE** (scheduled). The manifest has the active campaign URL.
3. Open Inbox, answer Amina's running-event refund request and approve it. Staff
   can reply to assigned cases; only the organizer/admin can complete refunds.
4. Show event lifecycle examples: Future Frames is an editable free draft with a
   hidden Crew preview type; Riverside Sounds is a paid draft awaiting activation;
   Cloud Native awaits admin review; Steppe Sessions is approved but unpublished;
   Taste Studio is rejected; Design Futures changed after approval and needs review.
5. Show private invitations, duplicate an event, edit an unsold draft, manage staff,
   and inspect the completed Founders Breakfast, Code Together and City Voices events.
6. Financial summaries show a recorded simulated payout, a processing payout, and
   a payout blocked by an open dispute. No actual transfers occur.

## Check-in and support staff demonstration

1. Sign in as Miras. Assigned events cover upcoming, live, past and cancelled states.
   **Community Makers: Open Studio** is live with valid and checked-in tickets.
2. Use `ticket_states` in the local manifest for a valid Aurora credential, a used
   Open Studio credential and a reversed Open Studio credential that is valid again.
   Admit a valid ticket, retry it, reverse admission, and show attendance/roster search.
   Refunded and cancelled ticket IDs are also listed; they have no usable QR in the UI.
3. Sign in as Dana for the separate support role. Ticket-delivery, accessibility,
   technical and group-arrival conversations are assigned to this account. It has
   support access on four events and no check-in assignment. Replies/status changes
   use the normal API and generate notifications.
4. Miras has a platform-support camera question. This conversation is separate
   from the attendee inbox and is assigned to the platform admin.

## Administrator demonstration

1. Approve the pending Robotics Society organizer application and refresh the
   applicant's session/status. Inspect the rejected application and approved histories.
2. Review Cloud Native's pending paid-event application; inspect approved, rejected,
   changes-required and not-submitted examples on organizer events.
3. Review the four formal reports and four disputes across open, under review,
   resolved and dismissed states. Decisions do not automatically refund or suspend.
4. Restore **Night Market** from whole-event suspension or **Arena Pulse** from
   paid-sales suspension. Restore the suspended user with an explanation. The
   cancelled **Mountain Echo** retains invalidated tickets and separate refund history.
5. Open operations for failed/expired/abandoned orders, inspect platform settings,
   answer organizer/staff platform conversations, and progress the processing payout.
   The already-paid transfer cannot be sent again; the disputed event stays blocked.

## Verification and recovery

The command smoke-tests attendee, organizer, staff, support and admin endpoints;
checks inventory against confirmed orders and active holds; verifies ticket/order
states; and runs SQLite foreign-key and integrity checks before replacement.
`demo_seed_manifest.json` records exact IDs, codes, counts and the backup path.
It is local generated data, excluded from Git along with backups.

To restore a previous database, stop the API and use SQLite's backup API to copy
the manifest's backup file onto the configured database; restart and sign in again.
Do not copy only a live database's main file while a WAL transaction is active.
