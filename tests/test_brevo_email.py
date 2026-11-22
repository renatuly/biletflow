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


def test_brevo_errors_include_actionable_hints(brevo_settings, caplog):
    import io
    import urllib.error

    error = urllib.error.HTTPError(
        email_module.BREVO_URL,
        401,
        "Unauthorized",
        {},
        io.BytesIO(b'{"message":"We have detected you are using an unrecognised IP address"}'),
    )
    with patch.object(email_module.urllib.request, "urlopen", side_effect=error):
        deliver(OutgoingEmail(to="a@example.com", subject="S", text="t", html="<p>t</p>"))
    assert "Authorised IPs" in caplog.text


def test_config_problems_flag_smtp_key_and_default_sender(monkeypatch):
    monkeypatch.setenv("EMAIL_BACKEND", "brevo")
    monkeypatch.setenv("BREVO_API_KEY", "xsmtpsib-abc")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "no-reply@biletflow.kz")
    get_settings.cache_clear()
    try:
        problems = " ".join(email_module.config_problems())
    finally:
        get_settings.cache_clear()
    assert "SMTP key" in problems and "verified in Brevo" in problems
    assert "xsmtpsib-abc" not in problems


def test_admin_email_test_reports_provider_failure(client, engine):
    from factories import API, account

    admin = account(client, engine, "root@example.com", role="platform_admin")
    user = account(client, engine, "user@example.com")
    assert client.post(f"{API}/admin/email/test", headers=user, json={}).status_code == 403
    with patch.object(email_module, "send", side_effect=RuntimeError("Brevo API returned 401")):
        result = client.post(f"{API}/admin/email/test", headers=admin, json={}).json()
    assert result["delivered"] is False and "401" in result["detail"]
    assert result["to"] == "root@example.com"
