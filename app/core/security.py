import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import get_settings


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    encoded_salt = base64.urlsafe_b64encode(salt).decode()
    encoded_digest = base64.urlsafe_b64encode(digest).decode()
    return f"scrypt${encoded_salt}${encoded_digest}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_text, digest_text = encoded.split("$", 2)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text)
        expected = base64.urlsafe_b64decode(digest_text)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _encode_part(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _decode_part(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_token(subject: str, purpose: str = "access", minutes: int | None = None) -> str:
    settings = get_settings()
    lifetime = minutes or settings.app_access_token_minutes
    payload = {
        "sub": subject,
        "purpose": purpose,
        "exp": int((datetime.now(UTC) + timedelta(minutes=lifetime)).timestamp()),
    }
    body = _encode_part(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(settings.app_secret_key.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_encode_part(signature)}"


def decode_token(token: str, purpose: str = "access") -> dict[str, Any] | None:
    try:
        body, signature = token.split(".", 1)
        expected = hmac.new(
            get_settings().app_secret_key.encode(), body.encode(), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(_decode_part(signature), expected):
            return None
        payload = json.loads(_decode_part(body))
        if (
            payload.get("purpose") != purpose
            or payload.get("exp", 0) < datetime.now(UTC).timestamp()
        ):
            return None
        return payload
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def create_qr_credential(ticket_id: str) -> str:
    return "ticket." + create_token(ticket_id, purpose="admission", minutes=60 * 24 * 365 * 5)


def parse_qr_credential(value: str) -> str | None:
    if not value.startswith("ticket."):
        return None
    payload = decode_token(value.removeprefix("ticket."), purpose="admission")
    return payload.get("sub") if payload else None
