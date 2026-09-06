from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import create_token, hash_password, verify_password
from app.users.models import Notification, User
from app.users.schemas import RegisterRequest


def register(db: Session, data: RegisterRequest) -> tuple[User, str]:
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
            message="Verify your BiletFlow email address.",
        )
    )
    db.commit()
    db.refresh(user)
    return user, create_token(str(user.id), "verify_email", 60 * 24)


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == email.lower()))
    return (
        user if user and user.is_active and verify_password(password, user.password_hash) else None
    )
