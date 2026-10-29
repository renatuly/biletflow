from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import create_token, hash_password, password_fingerprint, verify_password
from app.users.models import Notification, User
from app.users.schemas import RegisterRequest


def register(db: Session, data: RegisterRequest) -> User:
    email = data.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise ValueError("EMAIL_ALREADY_REGISTERED")
    user = User(
        email=email,
        password_hash=hash_password(data.password),
        full_name=data.full_name,
        preferred_locale=data.preferred_locale,
    )
    db.add(user)
    db.flush()
    db.add(
        Notification(
            user_id=user.id,
            kind="account_verification",
            title="Verify email",
            message="Check your inbox for a link to verify your BiletFlow email address.",
        )
    )
    db.commit()
    db.refresh(user)
    return user


def find_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email.lower()))


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = find_by_email(db, email)
    return (
        user if user and user.is_active and verify_password(password, user.password_hash) else None
    )


def access_token(user: User) -> str:
    return create_token(str(user.id), claims={"pwd": password_fingerprint(user.password_hash)})


def verification_token(user: User) -> str:
    # Bound to the address so a token cannot verify a later, different email.
    return create_token(
        str(user.id),
        "verify_email",
        get_settings().app_email_verification_hours * 60,
        {"email": user.email},
    )


def reset_token(user: User) -> str:
    # Bound to the current password hash, so the token stops working once it is used.
    return create_token(
        str(user.id),
        "reset_password",
        get_settings().app_password_reset_minutes,
        {"pwd": password_fingerprint(user.password_hash)},
    )


def reset_password(user: User, password: str) -> None:
    user.password_hash = hash_password(password)
    # Receiving the reset link proves control of the mailbox.
    user.email_verified = True
