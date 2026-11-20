import io
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from factories import (
    account,
    activate_paid_sales,
    campaign,
    checkout,
    complete,
    event,
    publish,
    ticket_type,
)
from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics
from sqlalchemy.orm import Session

from app.orders.models import Order
from app.platform.management import allocate_minor
from app.tickets.service import local_time, render_ticket_pdf, ticket_fonts


def pdf_fixture(long=False):
    title = "Қазақша концерт және русский фестиваль " * (6 if long else 2)
    e = SimpleNamespace(
        title=title,
        starts_at=datetime(2030, 1, 2, 12, tzinfo=UTC),
        ends_at=datetime(2030, 1, 2, 15, tzinfo=UTC),
        time_zone="Asia/Almaty",
    )
    v = SimpleNamespace(
        name="Өнер орталығы",
        address=("Длинный адрес көшесі " * 24 if long else "Абай көшесі 10"),
        city="Алматы",
    )
    t = SimpleNamespace(
        id=uuid4(),
        attendee_name="Әлия Ғұмарқызы <test> & Иван Иванов",
        qr_credential="ticket.test-signed-credential",
        status="valid",
        section="Ә" * (80 if long else 1),
        row="12",
        seat="15",
    )
    kind = SimpleNamespace(name="Жалпы кіру / Входной билет")
    return t, e, v, kind


@pytest.mark.parametrize("long", [False, True])
def test_pdf_preserves_unicode_long_fields_and_identity(long):
    t, e, v, kind = pdf_fixture(long)
    pdf = render_ticket_pdf(t, e, v, kind)
    pages = PdfReader(io.BytesIO(pdf)).pages
    text = " ".join(" ".join(p.extract_text().split()) for p in pages)
    for value in [e.title, v.name, v.address, t.attendee_name, kind.name, str(t.id)]:
        assert " ".join(value.split()) in text
    assert "17:00" in text
    assert "Status at issue: valid" in text
    assert len(pages) == 1 if not long else len(pages) <= 3
    # Verify A4 page size and embedded font resources as well as extracted text.
    for page in pages:
        assert float(page.mediabox.width) == pytest.approx(595.2756, abs=0.01)
        assert float(page.mediabox.height) == pytest.approx(841.8898, abs=0.01)
        fonts = page["/Resources"]["/Font"]
        assert any(
            "/FontFile2" in f.get_object().get("/FontDescriptor", {}) for f in fonts.values()
        )


def test_bundled_font_has_all_kazakh_and_russian_glyphs():
    ticket_fonts()
    glyphs = pdfmetrics.getFont("BiletFlowSans").face.charToGlyph
    assert all(ord(c) in glyphs for c in "ӘәҒғҚқҢңӨөҰұҮүҺһІіЁёЖжЩщЮюЯя")


def test_naive_database_dates_are_utc_not_server_local_time():
    assert local_time(datetime(2030, 1, 2, 12), "Asia/Almaty").hour == 17


def test_integer_allocation_is_exact_stable_and_does_not_charge_free_items():
    assert allocate_minor(1, {"b": 1, "a": 1, "free": 0}) == {"b": 0, "a": 1, "free": 0}
    assert sum(allocate_minor(10001, {"a": 123, "b": 456, "c": 0}).values()) == 10001
    assert allocate_minor(0, {"a": 0}) == {"a": 0}


def test_filtered_finances_reconcile_discounts_fees_refunds_and_campaign_series(client, engine):
    owner = account(client, engine, "org@demo.example", "organizer")
    buyer = account(client, engine, "buyer@demo.example")
    eid = event(client, owner)
    standard = ticket_type(client, owner, eid, "Standard", price_minor=100001)
    vip = ticket_type(client, owner, eid, "VIP", price_minor=100001)
    free = ticket_type(client, owner, eid, "Free")
    activate_paid_sales(client, engine, owner, eid)
    publish(client, owner, eid)
    assert campaign(
        client, owner, eid, discount_value=50, applicable_ticket_type_ids=[vip]
    ).is_success
    reservation = checkout(
        client, buyer, eid, [(standard, 1), (vip, 1), (free, 1)], "CAMPUS20"
    ).json()
    assert complete(client, buyer, reservation["id"]).is_success
    path = f"/api/v1/events/{eid}/analytics"

    def summary(kind=None):
        r = client.get(path, headers=owner, params={"ticket_type_id": kind} if kind else {})
        assert r.is_success, r.text
        return r.json()

    all_totals = summary()
    rows = [summary(k) for k in [standard, vip, free]]
    assert [r["discounts_minor"] for r in rows] == [0, 50000, 0]
    assert rows[2]["processing_fees_minor"] == 0
    for field in [
        "gross_sales_minor",
        "discounts_minor",
        "processing_fees_minor",
        "refunds_minor",
        "net_revenue_minor",
    ]:
        assert sum(r[field] for r in rows) == all_totals[field]
    # Even an odd fee must reconcile exactly and never use float rounding.
    with Session(engine) as db:
        o = db.get(Order, UUID(reservation["id"]))
        o.processing_fee_minor = 1
        o.total_minor = o.subtotal_minor - o.discount_minor + 1
        db.commit()
    rows = [summary(k) for k in [standard, vip, free]]
    assert sum(r["processing_fees_minor"] for r in rows) == 1
    refund = client.post(
        f"/api/v1/orders/{reservation['id']}/refunds",
        headers=owner,
        json={"reason": "Full demo refund"},
    )
    assert refund.is_success, refund.text
    rows = [summary(k) for k in [standard, vip, free]]
    assert sum(r["refunds_minor"] for r in rows) == summary()["refunds_minor"]
    for r in rows:
        assert r["net_revenue_minor"] == 0
        assert r["sales_series"][0]["revenue_minor"] == 0
        assert r["campaigns"][0]["revenue_minor"] == 0
