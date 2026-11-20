# Critical backend demo fixes

Date: 20 November 2026

Only printable ticket rendering and event-workspace financial allocation were changed.
No API response shape, payment/refund execution, admission credential, database schema,
web code or mobile code was changed.

## Printable tickets

The shared download/email PDF generator now embeds bundled Noto Sans regular/bold
with its SIL Open Font License. Full event titles, venue/address, ticket type,
attendee and seat fields wrap rather than truncate or overflow. User text is escaped
before passing to ReportLab. QR, canonical ticket ID and admission instructions stay
together. Ordinary samples fit one A4 page; unusually long records can use multiple
pages without dropping text. The admission credential and black-on-white QR are
unchanged. Naive database timestamps are treated as UTC before event-zone conversion.
Font assets are included in Python package data and in the existing Docker build.

## Financial allocation

Ticket-type filters now use stored OrderItem.discount_minor, preserving campaign
restrictions. Processing fees are allocated by discounted item amounts. Refunds
are allocated by each item’s paid contribution, including its fee share. Integer
largest-remainder allocation with stable item-ID tie-breaking prevents rounding
drift; free items receive no share when positive paid weights exist. Existing
unfiltered totals and fee-inclusive net-revenue semantics are retained.

For a 1,000 KZT Standard ticket plus 1,000 KZT VIP ticket with a VIP-only 50 percent
discount, filtered discounts are now 0 KZT and 500 KZT, respectively. Type-filtered
fees and refunds sum exactly to whole-order amounts. A full refund leaves zero
net revenue for every type, including the daily/campaign workspace series.
This reads existing recorded allocations; it does not repair inconsistent historical
orders manually altered outside normal checkout.

## Verification

- Full backend suite: 72 tests passed against isolated SQLite and in-memory email.
- Focused Ruff checks and formatting passed for changed Python files.
- Tests cover Cyrillic text preservation, font embedding/glyph coverage, long fields,
  canonical ticket identity, date conversion, restricted promotions, free items,
  odd-minor-unit rounding and full refunds.
- Visually inspected all three rendered sample pages: ordinary ticket (one page),
  long-field ticket (two pages). No clipped text or QR overlap observed.
- Apple Vision decoded the original signed credential from both PDFs rendered
  to grayscale at approximately 150 dpi. This is not a physical-print/device test.

Install current development dependencies with `pip install -e '.[dev]'` to obtain
the newly declared pypdf test dependency. Runtime dependencies did not change.
Restart the local API or rebuild its container before the demonstration.
Existing issued ticket IDs and credentials remain valid; newly downloaded/emailed
PDFs use the corrected layout.

Physical grayscale printing/camera scanning, SMTP receipt, PostgreSQL concurrent
load, deployment-domain links and backup/security acceptance still need their
normal demo-environment checks. No broader feature work was included.
