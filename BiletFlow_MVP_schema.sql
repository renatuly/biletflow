-- =====================================================================
-- BiletFlow — PostgreSQL Database Schema — MVP SCOPE ONLY
-- Based on SRS Section 8 "Required MVP Features"
--
-- EXCLUDED from this schema (SRS Section 8 "Bonus and Stretch Features"):
--   - Assigned seating / interactive seat maps → no Venue Section, Row,
--     Seat, or Seat Hold entities. Events are general-admission only.
--   - Calendar export (.ics) — no supporting entity needed anyway.
--   - Advanced GA4 analytics — external, not modeled in this DB.
--   - Offline check-in sync — online verification only for MVP.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS citext;    -- for case-insensitive email

-- ---------------------------------------------------------------------
-- ENUM TYPES
-- ---------------------------------------------------------------------
CREATE TYPE user_role              AS ENUM ('attendee', 'organizer', 'event_admin', 'platform_admin');
CREATE TYPE verification_status    AS ENUM ('unverified', 'pending', 'verified', 'rejected');
CREATE TYPE event_visibility       AS ENUM ('public', 'unlisted', 'private');
CREATE TYPE event_status           AS ENUM ('draft', 'published', 'unpublished', 'cancelled');
CREATE TYPE order_status           AS ENUM ('pending', 'paid', 'cancelled', 'refunded');
CREATE TYPE ticket_status          AS ENUM ('valid', 'checked_in', 'cancelled', 'refunded');
CREATE TYPE payment_status         AS ENUM ('pending', 'succeeded', 'failed');
CREATE TYPE refund_status          AS ENUM ('pending', 'completed', 'rejected');
CREATE TYPE checkin_action         AS ENUM ('check_in', 'undo');
CREATE TYPE staff_role             AS ENUM ('event_admin', 'organizer_staff');
CREATE TYPE discount_type          AS ENUM ('percentage', 'fixed_kzt');
CREATE TYPE campaign_status        AS ENUM ('active', 'disabled');
CREATE TYPE support_category       AS ENUM ('ticket_delivery', 'payment', 'refund',
                                             'event_information', 'check_in', 'account', 'technical');
CREATE TYPE support_status         AS ENUM ('open', 'in_progress', 'waiting_customer', 'resolved');
CREATE TYPE support_case_type      AS ENUM ('attendee', 'organizer');
CREATE TYPE notification_type      AS ENUM ('account_verification', 'order_confirmation', 'payment_failure',
                                             'ticket_delivery', 'event_update', 'event_cancellation',
                                             'refund_completion', 'payout_status', 'support_message',
                                             'support_status_change');
CREATE TYPE notification_channel   AS ENUM ('email', 'in_app');
CREATE TYPE notification_status    AS ENUM ('pending', 'sent', 'failed');

-- ---------------------------------------------------------------------
-- USER  &  ORGANIZER PROFILE  &  PAYOUT ACCOUNT
-- ---------------------------------------------------------------------
CREATE TABLE users (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email             CITEXT NOT NULL UNIQUE,
    password_hash     TEXT NOT NULL,
    full_name         VARCHAR(200),
    phone             VARCHAR(30),
    locale            VARCHAR(10) DEFAULT 'ru',
    email_verified_at TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE user_roles (
    user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role      user_role NOT NULL,
    PRIMARY KEY (user_id, role)
);
-- One user can hold multiple roles (e.g. attendee + organizer)

CREATE TABLE payout_accounts (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider          VARCHAR(50) NOT NULL,        -- e.g. 'sandbox'
    account_reference VARCHAR(200) NOT NULL,       -- opaque sandbox reference
    status            verification_status NOT NULL DEFAULT 'pending',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE organizer_profiles (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    contact_email       CITEXT,
    contact_phone       VARCHAR(30),
    payout_account_id   UUID REFERENCES payout_accounts(id),
    verification_status verification_status NOT NULL DEFAULT 'unverified',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- VENUE  (simple — no sections/rows/seats; assigned seating is bonus scope)
-- ---------------------------------------------------------------------
CREATE TABLE venues (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       VARCHAR(200) NOT NULL,
    address    VARCHAR(300),
    city       VARCHAR(100),
    timezone   VARCHAR(50) NOT NULL DEFAULT 'Asia/Almaty',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- EVENT  (general admission only for MVP)
-- ---------------------------------------------------------------------
CREATE TABLE events (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organizer_id            UUID NOT NULL REFERENCES organizer_profiles(id),
    venue_id                UUID REFERENCES venues(id),
    title                   VARCHAR(200) NOT NULL,
    description             TEXT,
    category                VARCHAR(100),
    cover_image_url         TEXT,
    visibility              event_visibility NOT NULL DEFAULT 'public',
    status                  event_status NOT NULL DEFAULT 'draft',
    capacity                INTEGER,
    registration_opens_at   TIMESTAMPTZ,
    registration_closes_at  TIMESTAMPTZ,
    starts_at               TIMESTAMPTZ NOT NULL,
    ends_at                 TIMESTAMPTZ,
    paid_sales_activated_at TIMESTAMPTZ,             -- NULL = paid sales not active
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- TICKET TYPE
-- ---------------------------------------------------------------------
CREATE TABLE ticket_types (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id        UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    name            VARCHAR(150) NOT NULL,
    description     TEXT,
    is_free         BOOLEAN NOT NULL DEFAULT false,
    price_kzt       NUMERIC(12,2) NOT NULL DEFAULT 0,
    quantity_total  INTEGER NOT NULL,
    quantity_sold   INTEGER NOT NULL DEFAULT 0,
    max_per_order   INTEGER NOT NULL DEFAULT 10,
    sales_start_at  TIMESTAMPTZ,
    sales_end_at    TIMESTAMPTZ,
    is_hidden       BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (quantity_sold <= quantity_total)
);

-- ---------------------------------------------------------------------
-- ORDER  &  ORDER ITEM  &  TICKET
-- ---------------------------------------------------------------------
CREATE TABLE orders (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id         UUID NOT NULL REFERENCES events(id),
    buyer_user_id    UUID REFERENCES users(id),        -- nullable if guest checkout is allowed
    buyer_email      CITEXT NOT NULL,
    status           order_status NOT NULL DEFAULT 'pending',
    total_amount     NUMERIC(12,2) NOT NULL DEFAULT 0,
    currency         VARCHAR(3) NOT NULL DEFAULT 'KZT',
    promo_code_id    UUID,                              -- FK added after promo_codes table exists
    discount_amount  NUMERIC(12,2) NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE order_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    ticket_type_id  UUID NOT NULL REFERENCES ticket_types(id),
    quantity        INTEGER NOT NULL DEFAULT 1,
    unit_price      NUMERIC(12,2) NOT NULL,
    subtotal        NUMERIC(12,2) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tickets (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_item_id  UUID NOT NULL REFERENCES order_items(id) ON DELETE CASCADE,
    attendee_name  VARCHAR(200) NOT NULL,
    attendee_email CITEXT,
    qr_code_token  UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(), -- signed on issue, see app layer
    status         ticket_status NOT NULL DEFAULT 'valid',
    issued_at      TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- One order_item with quantity > 1 produces multiple rows here — one per
-- physical ticket — because check-in status is tracked per individual
-- ticket, not per order line.

CREATE INDEX idx_tickets_qr_token ON tickets (qr_code_token);

-- ---------------------------------------------------------------------
-- PAYMENT  &  REFUND
-- ---------------------------------------------------------------------
CREATE TABLE payments (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id           UUID NOT NULL REFERENCES orders(id),
    provider           VARCHAR(50) NOT NULL DEFAULT 'sandbox',
    provider_reference VARCHAR(200),
    amount             NUMERIC(12,2) NOT NULL,
    status             payment_status NOT NULL DEFAULT 'pending',
    is_simulated       BOOLEAN NOT NULL DEFAULT true,
    processed_at       TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE refunds (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        UUID NOT NULL REFERENCES orders(id),
    payment_id      UUID NOT NULL REFERENCES payments(id),
    amount          NUMERIC(12,2) NOT NULL,
    reason          TEXT,
    status          refund_status NOT NULL DEFAULT 'pending',
    initiated_by    UUID NOT NULL REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- STAFF ASSIGNMENT  &  CHECK-IN RECORD
-- ---------------------------------------------------------------------
CREATE TABLE staff_assignments (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id     UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    user_id      UUID NOT NULL REFERENCES users(id),
    role         staff_role NOT NULL,
    assigned_by  UUID NOT NULL REFERENCES users(id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (event_id, user_id, role)
);

CREATE TABLE check_in_records (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id           UUID NOT NULL REFERENCES tickets(id),
    event_admin_user_id UUID NOT NULL REFERENCES users(id),
    action              checkin_action NOT NULL,
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Prevent double check-in with a single atomic statement:
--   UPDATE tickets SET status = 'checked_in'
--   WHERE id = $1 AND status = 'valid' RETURNING id;
-- Zero rows returned = ticket was already checked in (or invalid).
-- Insert into check_in_records happens only after this UPDATE succeeds.

-- ---------------------------------------------------------------------
-- NOTIFICATION
-- ---------------------------------------------------------------------
CREATE TABLE notifications (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID REFERENCES users(id),
    order_id   UUID REFERENCES orders(id),
    type       notification_type NOT NULL,
    channel    notification_channel NOT NULL,
    status     notification_status NOT NULL DEFAULT 'pending',
    payload    JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- PROMOTIONAL CAMPAIGN  &  PROMO CODE  &  PROMO REDEMPTION
-- ---------------------------------------------------------------------
CREATE TABLE promotional_campaigns (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id         UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    name             VARCHAR(150) NOT NULL,
    discount_type    discount_type NOT NULL,
    discount_value   NUMERIC(12,2) NOT NULL,
    valid_from       TIMESTAMPTZ NOT NULL,
    valid_until      TIMESTAMPTZ NOT NULL,
    max_redemptions  INTEGER NOT NULL,
    status           campaign_status NOT NULL DEFAULT 'active',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE campaign_ticket_types (
    campaign_id     UUID NOT NULL REFERENCES promotional_campaigns(id) ON DELETE CASCADE,
    ticket_type_id  UUID NOT NULL REFERENCES ticket_types(id) ON DELETE CASCADE,
    PRIMARY KEY (campaign_id, ticket_type_id)
);
-- Which ticket types a campaign's discount applies to

CREATE TABLE promo_codes (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id       UUID NOT NULL REFERENCES promotional_campaigns(id) ON DELETE CASCADE,
    code              VARCHAR(50) NOT NULL UNIQUE,
    campaign_qr_token UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(), -- distinct from ticket qr_code_token
    redemption_count  INTEGER NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (redemption_count >= 0)
);

ALTER TABLE orders
    ADD CONSTRAINT fk_orders_promo_code FOREIGN KEY (promo_code_id) REFERENCES promo_codes(id);

CREATE TABLE promo_redemptions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    promo_code_id    UUID NOT NULL REFERENCES promo_codes(id),
    order_id         UUID NOT NULL UNIQUE REFERENCES orders(id), -- one code application per order
    discount_amount  NUMERIC(12,2) NOT NULL,
    redeemed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Atomic redemption-limit enforcement — join to promotional_campaigns
-- for max_redemptions, then in one statement:
--   UPDATE promo_codes SET redemption_count = redemption_count + 1
--   WHERE id = $1 AND redemption_count < $max_redemptions AND status='active'
--     AND now() BETWEEN valid_from AND valid_until
--   RETURNING id;
-- Zero rows returned = limit reached, code inactive, or expired.

-- ---------------------------------------------------------------------
-- SUPPORT CASE  &  SUPPORT MESSAGE
-- ---------------------------------------------------------------------
CREATE TABLE support_cases (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_type        support_case_type NOT NULL,        -- attendee vs organizer support
    requester_id     UUID NOT NULL REFERENCES users(id),
    event_id         UUID REFERENCES events(id),
    order_id         UUID REFERENCES orders(id),
    ticket_id        UUID REFERENCES tickets(id),
    category         support_category NOT NULL,
    status           support_status NOT NULL DEFAULT 'open',
    assigned_to      UUID REFERENCES users(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE support_messages (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    support_case_id  UUID NOT NULL REFERENCES support_cases(id) ON DELETE CASCADE,
    sender_id        UUID NOT NULL REFERENCES users(id),
    body             TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- AUDIT LOG  (organizer event history / activity timeline)
-- ---------------------------------------------------------------------
CREATE TABLE audit_logs (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id             UUID REFERENCES events(id),
    actor_user_id        UUID REFERENCES users(id),
    action_type          VARCHAR(100) NOT NULL,      -- e.g. 'event.published', 'ticket.refunded'
    affected_entity_type VARCHAR(100) NOT NULL,
    affected_entity_id   UUID,
    description          TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_logs_event ON audit_logs (event_id, created_at);

-- ---------------------------------------------------------------------
-- Helpful indexes for common lookups
-- ---------------------------------------------------------------------
CREATE INDEX idx_events_organizer      ON events (organizer_id);
CREATE INDEX idx_events_status         ON events (status);
CREATE INDEX idx_ticket_types_event    ON ticket_types (event_id);
CREATE INDEX idx_orders_event          ON orders (event_id);
CREATE INDEX idx_orders_buyer          ON orders (buyer_user_id);
CREATE INDEX idx_order_items_order     ON order_items (order_id);
CREATE INDEX idx_tickets_order_item    ON tickets (order_item_id);
CREATE INDEX idx_support_cases_status  ON support_cases (status);
CREATE INDEX idx_support_messages_case ON support_messages (support_case_id, created_at);

-- =====================================================================
-- Notes for implementation (Nurdaulet):
-- 1. This schema covers MVP scope only (SRS Section 8, "Required MVP
--    Features"). Assigned seating (Venue Section / Row / Seat / Seat
--    Hold) is explicitly a Bonus/Stretch feature — add those tables
--    later only if the core flow is stable and there's time left
--    (see project plan, Phase 3/Week 9).
-- 2. Concurrency-sensitive operations (promo redemption, check-in) rely
--    on a single atomic SQL statement (UPDATE ... WHERE ... RETURNING),
--    not on separate check-then-write logic in application code.
-- 3. Ticket issuance (tickets + order_items rows) must happen inside
--    the same DB transaction as payment confirmation, per SRS 4.6.
-- 4. qr_code_token (tickets) and campaign_qr_token (promo_codes) are
--    intentionally separate columns/tables — the verification endpoint
--    must never accept a campaign_qr_token as a valid admission token.
-- 5. If/when assigned seating is added later (bonus phase), seat_id
--    (nullable) columns can be added back onto order_items and tickets
--    without breaking this MVP schema.
-- =====================================================================
