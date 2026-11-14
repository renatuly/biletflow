import json
from unittest.mock import patch

import pytest

from app.core import email as email_module
from app.core.config import Settings, get_settings
from app.core.email import Attachment, OutgoingEmail, deliver


@pytest.fixture
def brevo_settings(monkeypatch):
    monkeypatch.setenv("EMAIL_BACKEND", "brevo")
    monkeypatch.setenv("BREVO_API_KEY", "test-key")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "sender@example.com")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b'{"messageId":"x"}'


def test_brevo_sends_json_with_attachments_and_no_cid_images(brevo_settings):
    message = OutgoingEmail(
        to="guest@example.com",
        subject="Order confirmed",
        text="plain",
        html='<p>Hi</p><img src="cid:qr-1" width="180"><p>Bye</p>',
        attachments=[
            Attachment("qr.png", b"png-bytes", "image/png", "qr-1"),
            Attachment("ticket.pdf", b"pdf-bytes", "application/pdf"),
        ],
    )
    with patch.object(email_module.urllib.request, "urlopen", return_value=_Response()) as sent:
        deliver(message)
    request = sent.call_args.args[0]
    assert request.full_url == email_module.BREVO_URL
    assert request.get_header("Api-key") == "test-key"
    body = json.loads(request.data)
    assert body["sender"]["email"] == "sender@example.com"
    assert body["to"] == [{"email": "guest@example.com"}]
    assert "cid:" not in body["htmlContent"] and "QR code attached" in body["htmlContent"]
    assert [a["name"] for a in body["attachment"]] == ["qr.png", "ticket.pdf"]


def test_brevo_failure_is_logged_not_raised(brevo_settings, caplog):
    with patch.object(email_module.urllib.request, "urlopen", side_effect=OSError("down")):
        deliver(OutgoingEmail(to="a@example.com", subject="S", text="t", html="<p>t</p>"))
    assert "Email delivery to a@example.com failed" in caplog.text


def test_production_requires_brevo_key():
    with pytest.raises(ValueError, match="BREVO_API_KEY"):
        Settings(
            app_env="production",
            app_secret_key="x" * 40,
            email_backend="brevo",
            brevo_api_key=None,
            _env_file=None,
        )
