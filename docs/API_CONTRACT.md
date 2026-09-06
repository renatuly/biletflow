# BiletFlow API Contract

Status: Draft 1  
Base URL: `/api/v1`  
Format: JSON over HTTPS, except file downloads and QR images  
Currency: KZT  
Time format: ISO 8601 with UTC offsets; timestamps returned by the API are UTC  

This contract defines the boundary between the BiletFlow backend, web application,
and Event Admin mobile application. Endpoint details may be refined during
implementation, but incompatible changes require coordination with both clients.

## 1. Shared conventions

### 1.1 Authentication

Protected requests use:

```http
Authorization: Bearer <access-token>
```

Access tokens are short-lived. Refresh tokens are rotated. The final transport for
web refresh tokens should be an `HttpOnly`, `Secure`, `SameSite` cookie; native
clients store refresh credentials in platform-secure storage.

Roles:

- `attendee`
- `organizer`
- `event_admin`
- `platform_admin`

A user may have more than one role. Event-level authorization is determined from
ownership and staff assignments, not only from the global role.

### 1.2 Identifiers and money

- Public resource identifiers are UUIDs.
- Money is represented as integer minor units, never floating point.
- `amount: 150000` means `1,500.00 KZT` if the configured currency exponent is two.
- Every monetary object includes `currency: "KZT"`.

### 1.3 Pagination

Collection endpoints accept:

```text
?cursor=<opaque-cursor>&limit=20
```

`limit` defaults to 20 and has a maximum of 100.

```json
{
  "items": [],
  "next_cursor": null
}
```

### 1.4 Errors

All errors use one envelope:

```json
{
  "error": {
    "code": "TICKET_INVENTORY_EXHAUSTED",
    "message": "The selected ticket type is sold out.",
    "details": {},
    "request_id": "req_01..."
  }
}
```

Common status codes:

| Status | Meaning |
|---|---|
| `200` | Successful read or update |
| `201` | Resource created |
| `202` | Asynchronous operation accepted |
| `204` | Successful operation with no response body |
| `400` | Invalid workflow or malformed request |
| `401` | Authentication required or token invalid |
| `403` | Authenticated but not authorized |
| `404` | Resource not found or not visible to caller |
| `409` | State, inventory, seat, or idempotency conflict |
| `422` | Field validation failed |
| `429` | Rate limit exceeded |

### 1.5 Idempotency and concurrency

Payment confirmation, checkout completion, refunds, check-ins, and campaign
redemptions accept an `Idempotency-Key` header. Repeating a request with the same
key and payload returns the original outcome.

Inventory, seat holds, promo redemption limits, refunds, and check-ins must be
committed atomically by the server. Client-side availability is informational and
never authoritative.

### 1.6 Audit metadata

Resources generally contain:

```json
{
  "id": "uuid",
  "created_at": "2026-09-03T10:00:00Z",
  "updated_at": "2026-09-03T10:00:00Z"
}
```

Sensitive fields such as password hashes, payment credentials, raw QR signing
secrets, and internal fraud notes are never returned.

## 2. Health

| Method | Path | Access | Purpose |
|---|---|---|---|
| `GET` | `/health` | Public | Process liveness |
| `GET` | `/ready` | Internal/deployment | Database and required dependency readiness |

## 3. Authentication and accounts

| Method | Path | Access | Purpose |
|---|---|---|---|
| `POST` | `/auth/register` | Public | Register with email and password |
| `POST` | `/auth/verify-email` | Public | Verify email using a one-time token |
| `POST` | `/auth/resend-verification` | Public | Request another verification message |
| `POST` | `/auth/login` | Public | Issue access and refresh credentials |
| `POST` | `/auth/refresh` | Refresh credential | Rotate session credentials |
| `POST` | `/auth/logout` | Authenticated | Revoke current session |
| `POST` | `/auth/forgot-password` | Public | Request password-reset message |
| `POST` | `/auth/reset-password` | Public | Set password using a reset token |
| `GET` | `/auth/sessions` | Authenticated | List active sessions |
| `DELETE` | `/auth/sessions/{session_id}` | Authenticated | Revoke a session |

Registration request:

```json
{
  "email": "attendee@example.com",
  "password": "user-supplied password",
  "preferred_locale": "kk"
}
```

Login response:

```json
{
  "access_token": "token",
  "token_type": "bearer",
  "expires_in": 900,
  "user": {
    "id": "uuid",
    "email": "attendee@example.com",
    "email_verified": true,
    "roles": ["attendee"]
  }
}
```

## 4. Users and organizer profiles

| Method | Path | Access | Purpose |
|---|---|---|---|
| `GET` | `/users/me` | Authenticated | Read current user |
| `PATCH` | `/users/me` | Authenticated | Update name, phone, and locale |
| `GET` | `/organizer-profile` | Organizer | Read organizer profile |
| `PUT` | `/organizer-profile` | Organizer | Create or update contact information |
| `PUT` | `/organizer-profile/payout-account` | Organizer | Connect simulated/sandbox payout account |
| `GET` | `/organizer-profile/verification` | Organizer | Read verification status and missing fields |
| `POST` | `/organizer-profile/verification/submit` | Organizer | Submit simulated verification |

Organizer verification returns `not_started`, `pending`, `verified`, or `rejected`.
Payout responses contain provider references and masked details only.

## 5. Events and venues

| Method | Path | Access | Purpose |
|---|---|---|---|
| `GET` | `/events` | Public | Browse published public events |
| `GET` | `/events/{event_id}` | Public/authorized | Read event details |
| `POST` | `/events` | Organizer | Create draft event |
| `PATCH` | `/events/{event_id}` | Event manager | Edit event |
| `POST` | `/events/{event_id}/preview` | Event manager | Return publish-preview data |
| `POST` | `/events/{event_id}/publish` | Event manager | Publish event |
| `POST` | `/events/{event_id}/unpublish` | Event manager | Return event to draft/unlisted state |
| `POST` | `/events/{event_id}/cancel` | Event manager | Cancel event with reason |
| `POST` | `/events/{event_id}/duplicate` | Event manager | Copy configuration into a new draft |
| `GET` | `/organizer/events` | Organizer | List managed events by lifecycle status |
| `GET` | `/venue-layouts` | Organizer | List predefined seating layouts |
| `GET` | `/venue-layouts/{layout_id}` | Organizer | Read sections, rows, and seats |

Event lifecycle: `draft`, `published`, `cancelled`, `completed`. The organizer list
may derive UI groups such as Upcoming and Active from lifecycle and dates.

Core event write fields:

```json
{
  "title": "Example Event",
  "description": "Event description",
  "category": "conference",
  "visibility": "public",
  "venue": {
    "name": "Venue name",
    "address": "Full address"
  },
  "starts_at": "2026-10-10T18:00:00+05:00",
  "ends_at": "2026-10-10T21:00:00+05:00",
  "time_zone": "Asia/Almaty",
  "registration_opens_at": "2026-09-10T09:00:00+05:00",
  "registration_closes_at": "2026-10-10T17:00:00+05:00",
  "capacity": 500,
  "admission_type": "general_admission",
  "venue_layout_id": null,
  "refund_policy": "Organizer-defined policy"
}
```

Visibility values are `public`, `unlisted`, and `private`. Access to private events
uses a server-issued invitation or access token; visibility alone is not security.

### 5.1 Images

| Method | Path | Access | Purpose |
|---|---|---|---|
| `POST` | `/uploads/images` | Organizer | Request validated upload or upload image |
| `POST` | `/events/{event_id}/images` | Event manager | Attach uploaded image |
| `DELETE` | `/events/{event_id}/images/{image_id}` | Event manager | Remove image association |

## 6. Event staff assignments

| Method | Path | Access | Purpose |
|---|---|---|---|
| `GET` | `/events/{event_id}/staff` | Event manager | List assigned staff |
| `POST` | `/events/{event_id}/staff` | Event manager | Assign Event Admin by user/email |
| `PATCH` | `/events/{event_id}/staff/{assignment_id}` | Event manager | Change permissions |
| `DELETE` | `/events/{event_id}/staff/{assignment_id}` | Event manager | Revoke assignment |
| `GET` | `/check-in/events` | Event Admin | List only events assigned to current user |

Event staff permissions may include `check_in`, `reverse_check_in`,
`manage_attendees`, and `reply_to_support`.

## 7. Ticket types and inventory

| Method | Path | Access | Purpose |
|---|---|---|---|
| `GET` | `/events/{event_id}/ticket-types` | Public/authorized | List visible ticket types and availability |
| `POST` | `/events/{event_id}/ticket-types` | Event manager | Create free or paid ticket type |
| `PATCH` | `/events/{event_id}/ticket-types/{ticket_type_id}` | Event manager | Update ticket type |
| `POST` | `/events/{event_id}/ticket-types/{ticket_type_id}/hide` | Event manager | Hide without deleting |
| `POST` | `/events/{event_id}/ticket-types/{ticket_type_id}/show` | Event manager | Restore visibility |
| `GET` | `/events/{event_id}/inventory` | Event manager | View available, held, sold, refunded, checked-in counts |

Ticket type fields include name, description, `kind` (`free` or `paid`), price,
quantity, sales start/end, per-order limit, visibility, and optional price category.
Paid ticket types cannot be purchased unless paid sales are active for that event.

## 8. Assigned seating (bonus)

| Method | Path | Access | Purpose |
|---|---|---|---|
| `GET` | `/events/{event_id}/seat-map` | Public/authorized | Read layout and current display availability |
| `POST` | `/events/{event_id}/seat-holds` | Public/authenticated | Atomically hold selected seats |
| `GET` | `/seat-holds/{hold_id}` | Hold owner | Read hold and expiration |
| `DELETE` | `/seat-holds/{hold_id}` | Hold owner | Release hold early |

Seat-hold request:

```json
{
  "seat_ids": ["uuid"],
  "ticket_type_id": "uuid",
  "checkout_session_id": "uuid"
}
```

A successful response returns the authoritative price and `expires_at`. A conflict
returns `409 SEAT_UNAVAILABLE` with unavailable seat identifiers. Seat states are
`available`, `selected` (client-only), `held`, `sold`, `unavailable`, and
`accessible` as a separate attribute.

## 9. Paid-sales activation

| Method | Path | Access | Purpose |
|---|---|---|---|
| `GET` | `/events/{event_id}/paid-sales` | Event manager | Read status and activation checklist |
| `POST` | `/events/{event_id}/paid-sales/accept-terms` | Event manager | Accept versioned paid-event terms |
| `POST` | `/events/{event_id}/paid-sales/activation-fee` | Event manager | Run simulated/sandbox fee payment |
| `POST` | `/events/{event_id}/paid-sales/activate` | Event manager | Validate checklist and activate sales |

Statuses: `not_ready`, `ready`, `active`, `suspended`. Activation is event-specific.
The response clearly labels all academic simulation records with
`is_simulation: true`.

## 10. Checkout and orders

| Method | Path | Access | Purpose |
|---|---|---|---|
| `POST` | `/checkout/sessions` | Public/authenticated | Reserve inventory and start checkout |
| `GET` | `/checkout/sessions/{session_id}` | Session owner | Read totals, holds, and expiration |
| `PATCH` | `/checkout/sessions/{session_id}` | Session owner | Supply attendee and billing details |
| `POST` | `/checkout/sessions/{session_id}/apply-promo` | Session owner | Validate and apply promo token/code |
| `DELETE` | `/checkout/sessions/{session_id}/promo` | Session owner | Remove applied promotion |
| `POST` | `/checkout/sessions/{session_id}/complete` | Session owner | Complete free or simulated paid checkout |
| `GET` | `/orders` | Attendee | List current attendee's orders |
| `GET` | `/orders/{order_id}` | Order owner/event manager/admin | Read order and items |
| `GET` | `/events/{event_id}/orders` | Event manager | Search event orders |
| `GET` | `/events/{event_id}/attendees` | Event manager/staff | Search attendee list |
| `POST` | `/orders/{order_id}/cancel` | Authorized user | Cancel eligible free registration |

Checkout creation example:

```json
{
  "event_id": "uuid",
  "items": [
    {"ticket_type_id": "uuid", "quantity": 2, "seat_ids": []}
  ],
  "campaign_token": null
}
```

The server response is the authority for subtotal, discount, processing charge,
total, inventory expiration, and campaign attribution. Completing checkout returns
an order only after a zero-value registration succeeds or payment is confirmed.

Order statuses: `pending`, `confirmed`, `cancelled`, `partially_refunded`,
`refunded`, `payment_failed`, and `expired`.

## 11. Payments, refunds, and payouts

| Method | Path | Access | Purpose |
|---|---|---|---|
| `POST` | `/checkout/sessions/{session_id}/payment-intent` | Session owner | Create sandbox/simulated payment attempt |
| `GET` | `/orders/{order_id}/payments` | Order owner/event manager/admin | List masked payment records |
| `POST` | `/orders/{order_id}/refunds` | Event manager/admin | Initiate eligible full refund |
| `GET` | `/orders/{order_id}/refunds` | Order owner/event manager/admin | List refund records |
| `GET` | `/events/{event_id}/financial-summary` | Event manager/admin | Gross, fees, discounts, refunds, estimated payout |
| `GET` | `/organizer/payouts` | Organizer | List simulated payout statuses |
| `POST` | `/webhooks/payments/{provider}` | Verified provider | Receive provider sandbox events |

Payment statuses: `pending`, `succeeded`, `failed`, `cancelled`, and `refunded`.
Refund statuses: `pending`, `succeeded`, and `failed`. Payment-card data is handled
by the provider and never accepted or stored by BiletFlow endpoints.

## 12. Issued tickets and files

| Method | Path | Access | Purpose |
|---|---|---|---|
| `GET` | `/tickets` | Attendee | List current attendee's issued tickets |
| `GET` | `/tickets/{ticket_id}` | Ticket owner/event manager/admin | Read canonical ticket |
| `GET` | `/tickets/{ticket_id}/pdf` | Ticket owner/event manager/admin | Download print-optimized PDF |
| `GET` | `/tickets/{ticket_id}/qr` | Ticket owner/event manager/admin | Download/display admission QR image |
| `POST` | `/tickets/{ticket_id}/resend` | Ticket owner/event manager | Resend delivery email |

Ticket statuses: `valid`, `checked_in`, `cancelled`, and `refunded`. The admission QR
contains an opaque or signed ticket credential, not attendee or payment details.
Digital and printed copies reference the same canonical ticket identifier.

## 13. Mobile verification and check-in

| Method | Path | Access | Purpose |
|---|---|---|---|
| `GET` | `/check-in/events/{event_id}/summary` | Assigned Event Admin | Registered and checked-in counts |
| `POST` | `/check-in/events/{event_id}/validate` | Assigned Event Admin | Validate scanned admission credential |
| `POST` | `/check-in/events/{event_id}/admit` | Assigned Event Admin | Atomically validate and record check-in |
| `GET` | `/check-in/events/{event_id}/attendees` | Assigned Event Admin | Manual attendee/ticket search |
| `POST` | `/check-in/records/{record_id}/reverse` | Authorized Event Admin | Reverse accidental check-in |

Admission request:

```json
{
  "qr_payload": "opaque-ticket-credential",
  "scanned_at": "2026-10-10T13:02:01Z",
  "device_id": "installation-identifier"
}
```

The result code is one of `valid`, `invalid`, `cancelled`, `refunded`,
`already_used`, or `wrong_event`. A Campaign QR credential always returns
`invalid`/`CAMPAIGN_QR_NOT_ADMISSION`. Concurrent duplicate admits produce exactly
one successful check-in.

## 14. Promotional campaigns and codes

| Method | Path | Access | Purpose |
|---|---|---|---|
| `GET` | `/events/{event_id}/campaigns` | Event manager | List campaigns and performance |
| `POST` | `/events/{event_id}/campaigns` | Event manager | Create campaign and server-generated promo code |
| `GET` | `/events/{event_id}/campaigns/{campaign_id}` | Event manager | Read campaign |
| `PATCH` | `/events/{event_id}/campaigns/{campaign_id}` | Event manager | Change dates, limits, or enabled state |
| `GET` | `/events/{event_id}/campaigns/{campaign_id}/qr` | Event manager | Download distinct Campaign QR image |
| `GET` | `/campaigns/resolve/{opaque_token}` | Public | Resolve link to event and validated promotion |
| `POST` | `/promo-codes/validate` | Checkout session owner | Validate code against current basket |

Discount types are `percentage` and `fixed_kzt`. The backend validates dates,
ticket applicability, maximum redemptions, and totals. Campaign URLs contain only
an opaque token. They never contain a trusted discount amount.

## 15. Support cases and messages

| Method | Path | Access | Purpose |
|---|---|---|---|
| `POST` | `/support/cases` | Attendee/organizer | Open contextual support case |
| `GET` | `/support/cases` | Authenticated | List only cases visible to caller |
| `GET` | `/support/cases/{case_id}` | Case participant/staff/admin | Read case and context |
| `GET` | `/support/cases/{case_id}/messages` | Authorized | List message thread |
| `POST` | `/support/cases/{case_id}/messages` | Authorized | Add message |
| `PATCH` | `/support/cases/{case_id}/status` | Authorized staff | Change workflow status |
| `PATCH` | `/support/cases/{case_id}/assignment` | Authorized staff | Assign case |

Categories: `ticket_delivery`, `payment`, `refund`, `seating`,
`event_information`, `check_in`, `account`, and `technical`.

Statuses: `open`, `in_progress`, `waiting_for_customer`, and `resolved`.
Creation accepts optional `event_id`, `order_id`, and `ticket_id`; the server checks
the requester's relationship before attaching their context.

## 16. Notifications

| Method | Path | Access | Purpose |
|---|---|---|---|
| `GET` | `/notifications` | Authenticated | List in-app notifications |
| `GET` | `/notifications/unread-count` | Authenticated | Read unread total |
| `POST` | `/notifications/{notification_id}/read` | Owner | Mark one as read |
| `POST` | `/notifications/read-all` | Authenticated | Mark all visible notifications as read |
| `GET` | `/notification-preferences` | Authenticated | Read channel/locale preferences |
| `PATCH` | `/notification-preferences` | Authenticated | Update optional preferences |

Security and transactional messages may not be disabled where delivery is required
for account or purchase operation.

## 17. Calendar export (bonus)

| Method | Path | Access | Purpose |
|---|---|---|---|
| `GET` | `/events/{event_id}/calendar.ics` | Public/authorized | Download stable iCalendar event |
| `GET` | `/events/{event_id}/calendar-links` | Public/authorized | Return encoded common-calendar links |

The `.ics` response preserves the configured time zone and stable UID. Cancelled
events return an updated entry with cancellation status and incremented sequence.

## 18. Organizer analytics and history

| Method | Path | Access | Purpose |
|---|---|---|---|
| `GET` | `/organizer/analytics/summary` | Organizer | Capacity, sales, revenue, and attendance totals |
| `GET` | `/organizer/analytics/sales-series` | Organizer | Sales grouped over time |
| `GET` | `/organizer/analytics/ticket-types` | Organizer | Compare ticket types |
| `GET` | `/organizer/analytics/campaigns` | Organizer | Campaign attribution and revenue |
| `GET` | `/organizer/analytics/attendance` | Organizer | Checked-in, absent, and percentage |
| `GET` | `/events/{event_id}/history` | Event manager/admin | Filter immutable event activity timeline |

Analytics accept authorized `event_id`, `date_from`, `date_to`, and optional
`ticket_type_id`. Revenue responses include gross, discount, refunds, fees, and net
demonstration revenue. Required metrics come from BiletFlow transactional records,
not GA4.

History entries contain timestamp, actor summary, action type, affected entity, and
short description. No normal API supports modifying or deleting audit entries.

## 19. Platform administration

All endpoints in this section require `platform_admin`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/admin/users` | Search users |
| `GET` | `/admin/users/{user_id}` | Review user |
| `POST` | `/admin/users/{user_id}/suspend` | Suspend user with reason |
| `POST` | `/admin/users/{user_id}/restore` | Restore user |
| `GET` | `/admin/events` | Search and review events |
| `POST` | `/admin/events/{event_id}/suspend` | Suspend event and sales |
| `POST` | `/admin/events/{event_id}/restore` | Restore eligible event |
| `GET` | `/admin/reports/events` | Review reported events |
| `PATCH` | `/admin/reports/events/{report_id}` | Record moderation outcome |
| `GET` | `/admin/paid-sales-activations` | Inspect activation records |
| `POST` | `/admin/events/{event_id}/paid-sales/suspend` | Suspend paid sales |
| `GET` | `/admin/payments` | Search simulated payment records |
| `GET` | `/admin/refunds` | Monitor refunds |
| `GET` | `/admin/disputes` | Monitor sandbox/simulated disputes |
| `GET` | `/admin/support/cases` | Review/escalate support cases |
| `GET` | `/admin/campaigns` | Review promotional activity |
| `GET` | `/admin/settings` | Read platform settings |
| `PATCH` | `/admin/settings` | Update activation fee and safe settings |
| `GET` | `/admin/reports/operations` | Basic operational report |
| `GET` | `/admin/reports/operations.csv` | Export operational report |

Suspending an event prevents new checkout and paid sales but retains historical
records. Existing tickets require an explicit policy decision; suspension alone
does not silently invalidate admissions.

## 20. Audit logs

| Method | Path | Access | Purpose |
|---|---|---|---|
| `GET` | `/events/{event_id}/audit-logs` | Event manager/admin | Event-scoped audit records |
| `GET` | `/admin/audit-logs` | Platform Admin | Search platform audit records |

Audit entries record authentication-sensitive and organizer/admin actions,
including activation, payment/refund commands, ticket invalidation, check-in
reversal, moderation, support assignment, and setting changes.

## 21. Localization and representation

- Supported initial locale codes: `kk`, `ru`, and `en`.
- Clients send `Accept-Language`; user preference is used as fallback.
- Stable machine-readable error codes do not change by locale.
- Event content may initially be stored in one organizer-provided language. A future
  localized-content structure must not require changing endpoint identities.

## 22. Deferred and excluded APIs

The initial contract intentionally excludes production KYC/KYB, real payouts,
ticket resale/transfer, recurring events, multiple payout splits, tax accounting,
arbitrary venue-layout editing, native attendee applications, and production-grade
offline check-in synchronization.

Bonus endpoints for assigned seating and calendar export may be omitted from the
term implementation without changing the required MVP contract.

## 23. Recommended implementation order

1. Health, database foundation, authentication, and users
2. Organizer profiles, events, and ticket types
3. Inventory holds, checkout, orders, simulated payments, and ticket issuance
4. Ticket PDF/QR delivery and online mobile check-in
5. Campaigns, support cases, notifications, administration, analytics, and history
6. Assigned seating and calendar export only after the core flow is stable
