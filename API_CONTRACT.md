# BiletFlow — API Contract

**Version:** MVP draft
**Base URL:** `/api/v1`
**Format:** REST, JSON request/response bodies

Scope note: modules marked **Bonus** are not part of the MVP (SRS Section 8) — implement only if core scope is complete and stable (see project plan, Phase 3 / Week 9).

---

## Table of Contents

1. [Conventions](#1-conventions)
2. [Health](#2-health)
3. [Authentication & Accounts](#3-authentication--accounts)
4. [Users & Organizer Profile](#4-users--organizer-profile)
5. [Events & Venues](#5-events--venues)
6. [Event Staff Assignments](#6-event-staff-assignments)
7. [Ticket Types & Inventory](#7-ticket-types--inventory)
8. [Assigned Seating — Bonus](#8-assigned-seating--bonus)
9. [Paid-Sales Activation](#9-paid-sales-activation)
10. [Checkout & Orders](#10-checkout--orders)
11. [Payments, Refunds & Payouts](#11-payments-refunds--payouts)
12. [Issued Tickets & Files](#12-issued-tickets--files)
13. [Mobile Verification & Check-in](#13-mobile-verification--check-in)
14. [Promotional Campaigns & Codes](#14-promotional-campaigns--codes)
15. [Support Cases & Messages](#15-support-cases--messages)
16. [Notifications](#16-notifications)
17. [Calendar Export — Bonus](#17-calendar-export--bonus)
18. [Organizer Analytics & History](#18-organizer-analytics--history)
19. [Platform Administration](#19-platform-administration)
20. [Audit Logs](#20-audit-logs)
21. [Open Items to Resolve](#21-open-items-to-resolve)

---

## 1. Conventions

**Auth header** — every protected endpoint:
```
Authorization: Bearer <token>
```
Roles: `attendee`, `organizer`, `event_admin`, `platform_admin`. One user may hold multiple roles.

**IDs** — UUID everywhere.

**Money** — integer minor units, not float. `150000` = 1500.00 KZT. Every money-bearing object includes `"currency": "KZT"`.

**Error format** — consistent shape on every failure:
```json
{
  "error": {
    "code": "SEAT_UNAVAILABLE",
    "message": "This seat is no longer available.",
    "request_id": "..."
  }
}
```

**Common status codes:**

| Code | Meaning |
|---|---|
| 400 | Malformed request |
| 401 | Missing/invalid auth |
| 403 | Authenticated but not authorized |
| 404 | Resource not found |
| 409 | Conflict (e.g. resource already taken/redeemed) |
| 422 | Validation failed |
| 429 | Rate limited |

**Idempotency** — `Idempotency-Key` header required on: checkout completion, refund creation, check-in admit, promo redemption. Prevents duplicate side effects on client retry.

**Concurrency** — inventory, redemption limits, and check-in status are enforced atomically on the server (single `UPDATE ... WHERE ... RETURNING` statement). Client-side availability is informational only, never authoritative.

---

## 2. Health

*Owner: infra (Alikhan)*

| Method | Path | Access | Description |
|---|---|---|---|
| `GET` | `/health` | Public | Process liveness |
| `GET` | `/ready` | Internal | DB and dependency readiness |

---

## 3. Authentication & Accounts

*Owner: Nursultan*

| Method | Path | Access | Description |
|---|---|---|---|
| `POST` | `/auth/register` | Public | Register with email + password |
| `POST` | `/auth/verify-email` | Public | Verify email via one-time token |
| `POST` | `/auth/resend-verification` | Public | Request another verification message |
| `POST` | `/auth/login` | Public | Issue access + refresh credentials |
| `POST` | `/auth/refresh` | Refresh token | Rotate session credentials |
| `POST` | `/auth/logout` | Authenticated | Revoke current session |
| `POST` | `/auth/forgot-password` | Public | Request password-reset message |
| `POST` | `/auth/reset-password` | Public | Set new password using reset token |
| `GET` | `/auth/sessions` | Authenticated | List active sessions |
| `DELETE` | `/auth/sessions/{session_id}` | Authenticated | Revoke a session |

---

## 4. Users & Organizer Profile

*Owner: Nursultan*

| Method | Path | Access | Description |
|---|---|---|---|
| `GET` | `/users/me` | Authenticated | Current user |
| `PATCH` | `/users/me` | Authenticated | Update name/phone/locale |
| `GET` | `/organizer-profile` | Organizer | Organizer profile |
| `PATCH` | `/organizer-profile` | Organizer | Create/update contact info |
| `PATCH` | `/organizer-profile/payout-account` | Organizer | Attach sandbox payout account |
| `GET` | `/organizer-profile/verification` | Organizer | Verification status |
| `POST` | `/organizer-profile/verification/submit` | Organizer | Submit simulated verification |

---

## 5. Events & Venues

*Owner: Nursultan*

| Method | Path | Access | Description |
|---|---|---|---|
| `GET` | `/events` | Public | List published events |
| `GET` | `/events/{event_id}` | Public/auth | Event details |
| `POST` | `/events` | Organizer | Create draft event |
| `PATCH` | `/events/{event_id}` | Event manager | Edit event |
| `POST` | `/events/{event_id}/preview` | Event manager | Get preview data before publishing |
| `POST` | `/events/{event_id}/publish` | Event manager | Publish |
| `POST` | `/events/{event_id}/unpublish` | Event manager | Revert to draft |
| `POST` | `/events/{event_id}/cancel` | Event manager | Cancel with reason |
| `POST` | `/events/{event_id}/duplicate` | Event manager | Duplicate into a new draft |
| `GET` | `/organizer/events` | Organizer | My events by status |
| `GET` | `/venue-layouts` | Organizer | **Bonus** — predefined seating layouts |
| `GET` | `/venue-layouts/{layout_id}` | Organizer | **Bonus** — layout sections/rows/seats |
| `POST` | `/uploads/images` | Organizer | Upload an image |
| `POST` | `/events/{event_id}/images` | Event manager | Attach image to event |
| `DELETE` | `/events/{event_id}/images/{image_id}` | Event manager | Remove image attachment |

---

## 6. Event Staff Assignments

*Owner: Nursultan*

| Method | Path | Access | Description |
|---|---|---|---|
| `GET` | `/events/{event_id}/staff` | Event manager | List assigned staff |
| `POST` | `/events/{event_id}/staff` | Event manager | Assign an Event Admin |
| `PATCH` | `/events/{event_id}/staff/{assignment_id}` | Event manager | Change permissions |
| `DELETE` | `/events/{event_id}/staff/{assignment_id}` | Event manager | Revoke assignment |
| `GET` | `/check-in/events` | Event Admin | Mobile — my assigned events only |

---

## 7. Ticket Types & Inventory

*Owner: Nursultan*

| Method | Path | Access | Description |
|---|---|---|---|
| `GET` | `/events/{event_id}/ticket-types` | Public/auth | List types and availability |
| `POST` | `/events/{event_id}/ticket-types` | Event manager | Create free/paid type |
| `PATCH` | `/events/{event_id}/ticket-types/{ticket_type_id}` | Event manager | Update type |
| `POST` | `/events/{event_id}/ticket-types/{ticket_type_id}/hide` | Event manager | Hide without deleting |
| `POST` | `/events/{event_id}/ticket-types/{ticket_type_id}/show` | Event manager | Restore visibility |
| `GET` | `/events/{event_id}/inventory` | Event manager | Available/held/sold/refunded/checked-in counts |

---

## 8. Assigned Seating — Bonus

*Owner: Nursultan (bonus phase only)*

| Method | Path | Access | Description |
|---|---|---|---|
| `GET` | `/events/{event_id}/seat-map` | Public/auth | Layout and seat availability |
| `POST` | `/events/{event_id}/seat-holds` | Public/auth | Atomically hold seats |
| `GET` | `/seat-holds/{hold_id}` | Hold owner | Read hold and expiration |
| `DELETE` | `/seat-holds/{hold_id}` | Hold owner | Release early |

---

## 9. Paid-Sales Activation

*Owner: Nursultan*

| Method | Path | Access | Description |
|---|---|---|---|
| `GET` | `/events/{event_id}/paid-sales` | Event manager | Activation checklist and status |
| `POST` | `/events/{event_id}/paid-sales/accept-terms` | Event manager | Accept terms |
| `POST` | `/events/{event_id}/paid-sales/activation-fee` | Event manager | Simulated fee payment |
| `POST` | `/events/{event_id}/paid-sales/activate` | Event manager | Verify checklist and activate |

---

## 10. Checkout & Orders

*Owner: Nursultan*

| Method | Path | Access | Description |
|---|---|---|---|
| `POST` | `/checkout/sessions` | Public/auth | Reserve inventory, start checkout |
| `GET` | `/checkout/sessions/{session_id}` | Session owner | Totals, holds, expiration |
| `PATCH` | `/checkout/sessions/{session_id}` | Session owner | Attendee/billing details |
| `POST` | `/checkout/sessions/{session_id}/apply-promo` | Session owner | Validate and apply promo code |
| `DELETE` | `/checkout/sessions/{session_id}/promo` | Session owner | Remove promo code |
| `POST` | `/checkout/sessions/{session_id}/complete` | Session owner | Complete free/simulated-paid checkout |
| `GET` | `/orders` | Attendee | My orders |
| `GET` | `/orders/{order_id}` | Owner/manager/admin | Order and line items |
| `GET` | `/events/{event_id}/orders` | Event manager | Search event orders |
| `GET` | `/events/{event_id}/attendees` | Event manager/staff | Search attendee list |
| `POST` | `/orders/{order_id}/cancel` | Authorized | Cancel a free registration |

---

## 11. Payments, Refunds & Payouts

*Owner: Nursultan*

| Method | Path | Access | Description |
|---|---|---|---|
| `POST` | `/checkout/sessions/{session_id}/payment-intent` | Session owner | Create sandbox payment |
| `GET` | `/orders/{order_id}/payments` | Owner/manager/admin | Masked payment records |
| `POST` | `/orders/{order_id}/refunds` | Event manager/admin | Initiate full refund |
| `GET` | `/orders/{order_id}/refunds` | Owner/manager/admin | List refunds |
| `GET` | `/events/{event_id}/financial-summary` | Event manager/admin | Gross, fees, discounts, payout |
| `GET` | `/organizer/payouts` | Organizer | Simulated payout statuses |
| `POST` | `/webhooks/payments/{provider}` | Verified provider | Receive sandbox payment events |

---

## 12. Issued Tickets & Files

*Owner: Nursultan*

| Method | Path | Access | Description |
|---|---|---|---|
| `GET` | `/tickets` | Attendee | My issued tickets |
| `GET` | `/tickets/{ticket_id}` | Owner/manager/admin | Canonical ticket record |
| `GET` | `/tickets/{ticket_id}/pdf` | Owner/manager/admin | Printable PDF |
| `GET` | `/tickets/{ticket_id}/qr` | Owner/manager/admin | QR image |
| `POST` | `/tickets/{ticket_id}/resend` | Owner/manager | Resend delivery email |

---

## 13. Mobile Verification & Check-in

*Owner: Nursultan*

| Method | Path | Access | Description |
|---|---|---|---|
| `GET` | `/check-in/events/{event_id}/summary` | Assigned Event Admin | Registered/checked-in counts |
| `POST` | `/check-in/events/{event_id}/validate` | Assigned Event Admin | Check QR without recording |
| `POST` | `/check-in/events/{event_id}/admit` | Assigned Event Admin | Atomically validate + record check-in |
| `GET` | `/check-in/events/{event_id}/attendees` | Assigned Event Admin | Manual attendee search |
| `POST` | `/check-in/records/{record_id}/reverse` | Authorized Event Admin | Undo an accidental check-in |

> ⚠️ The verification endpoints above must **never** accept a Campaign QR token as a valid admission credential — see [Section 14](#14-promotional-campaigns--codes).

---

## 14. Promotional Campaigns & Codes

*Owner: Nurdaulet*

| Method | Path | Access | Description |
|---|---|---|---|
| `GET` | `/events/{event_id}/campaigns` | Event manager | List campaigns and performance |
| `POST` | `/events/{event_id}/campaigns` | Event manager | Create campaign + promo code |
| `GET` | `/events/{event_id}/campaigns/{campaign_id}` | Event manager | Read campaign |
| `PATCH` | `/events/{event_id}/campaigns/{campaign_id}` | Event manager | Dates, limits, status |
| `GET` | `/events/{event_id}/campaigns/{campaign_id}/qr` | Event manager | Campaign QR image |
| `GET` | `/campaigns/resolve/{opaque_token}` | Public | Resolve link to event + promo |
| `POST` | `/promo-codes/validate` | Session owner | Validate code against current cart |

---

## 15. Support Cases & Messages

*Owner: Nurdaulet*

| Method | Path | Access | Description |
|---|---|---|---|
| `POST` | `/support/cases` | Attendee/organizer | Open a contextual case |
| `GET` | `/support/cases` | Authenticated | Cases I can access |
| `GET` | `/support/cases/{case_id}` | Participant/staff/admin | Case and its context |
| `GET` | `/support/cases/{case_id}/messages` | Authorized | Message thread |
| `POST` | `/support/cases/{case_id}/messages` | Authorized | Add a message |
| `PATCH` | `/support/cases/{case_id}/status` | Authorized staff | Change status |
| `PATCH` | `/support/cases/{case_id}/assignment` | Authorized staff | Assign case |

---

## 16. Notifications

*Owner: Nurdaulet*

| Method | Path | Access | Description |
|---|---|---|---|
| `GET` | `/notifications` | Authenticated | List in-app notifications |
| `GET` | `/notifications/unread-count` | Authenticated | Unread count |
| `POST` | `/notifications/{notification_id}/read` | Owner | Mark as read |
| `POST` | `/notifications/read-all` | Authenticated | Mark all as read |
| `GET` | `/notification-preferences` | Authenticated | Channel/locale settings |
| `PATCH` | `/notification-preferences` | Authenticated | Update settings |

---

## 17. Calendar Export — Bonus

*Owner: Nurdaulet (bonus phase only)*

| Method | Path | Access | Description |
|---|---|---|---|
| `GET` | `/events/{event_id}/calendar.ics` | Public/auth | Download .ics with stable UID |
| `GET` | `/events/{event_id}/calendar-links` | Public/auth | Links to common calendar services |

---

## 18. Organizer Analytics & History

*Owner: Nurdaulet*

| Method | Path | Access | Description |
|---|---|---|---|
| `GET` | `/organizer/analytics/summary` | Organizer | Capacity, sales, revenue, attendance |
| `GET` | `/organizer/analytics/sales-series` | Organizer | Sales over time |
| `GET` | `/organizer/analytics/ticket-types` | Organizer | Ticket type comparison |
| `GET` | `/organizer/analytics/campaigns` | Organizer | Campaign attribution and revenue |
| `GET` | `/organizer/analytics/attendance` | Organizer | Checked-in, absent, percentage |
| `GET` | `/events/{event_id}/history` | Event manager/admin | Event activity timeline |

---

## 19. Platform Administration

*Owner: Nurdaulet*

| Method | Path | Access | Description |
|---|---|---|---|
| `GET` | `/admin/users` | Platform admin | Search users |
| `GET` | `/admin/users/{user_id}` | Platform admin | View user |
| `POST` | `/admin/users/{user_id}/suspend` | Platform admin | Suspend with reason |
| `POST` | `/admin/users/{user_id}/restore` | Platform admin | Restore |
| `GET` | `/admin/events` | Platform admin | Search/view events |
| `POST` | `/admin/events/{event_id}/suspend` | Platform admin | Suspend sales |
| `POST` | `/admin/events/{event_id}/restore` | Platform admin | Restore event |
| `GET` | `/admin/reports/events` | Platform admin | Reported events |
| `PATCH` | `/admin/reports/events/{report_id}` | Platform admin | Moderation outcome |
| `GET` | `/admin/paid-sales-activations` | Platform admin | Activation records |
| `POST` | `/admin/events/{event_id}/paid-sales/suspend` | Platform admin | Suspend paid sales |
| `GET` | `/admin/payments` | Platform admin | Simulated payments |
| `GET` | `/admin/refunds` | Platform admin | Refund monitoring |
| `GET` | `/admin/disputes` | Platform admin | Sandbox disputes |
| `GET` | `/admin/support/cases` | Platform admin | Escalated support cases |
| `GET` | `/admin/campaigns` | Platform admin | Promo activity overview |
| `GET` | `/admin/settings` | Platform admin | Platform settings |
| `PATCH` | `/admin/settings` | Platform admin | Activation fee etc. |
| `GET` | `/admin/reports/operations` | Platform admin | Basic operational report |
| `GET` | `/admin/reports/operations.csv` | Platform admin | Export report |

---

## 20. Audit Logs

*Owner: Nurdaulet*

| Method | Path | Access | Description |
|---|---|---|---|
| `GET` | `/events/{event_id}/audit-logs` | Event manager/admin | Audit trail scoped to one event |
| `GET` | `/admin/audit-logs` | Platform admin | Platform-wide audit search |

---

## 21. Open Items to Resolve

- [ ] **Money format** — contract uses integer minor units; DB schema currently uses `NUMERIC(12,2)`. Pick one before implementation.
- [ ] **Idempotency-Key storage** — needs a dedicated table to store keys + cached results; not yet in the DB schema.
- [ ] **Naming alignment** — `admission_type` (contract) vs `seating_type` (schema); `campaign_token` (contract) vs `promo_codes.campaign_qr_token` (schema). Converge on one glossary.
- [ ] **Implementation order** — auth → events/tickets → checkout/orders/tickets → check-in → campaigns/support/analytics → bonus. Matches the project plan's Phase 2/3 breakdown; confirm with the team.
