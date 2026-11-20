import io
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.tickets.service import local_time, render_ticket_pdf, ticket_fonts
from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics


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
