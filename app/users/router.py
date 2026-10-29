from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.dependencies import current_user
from app.core.email import queue
from app.core.security import decode_token, password_fingerprint
from app.notifications.emails import (
    password_changed_email,
    password_reset_email,
    verification_email,
)
from app.users.models import Notification, User
from app.users.schemas import (
    EmailRequest,
    LoginRequest,
    PasswordResetConfirm,
    RegisterRequest,
    TokenRequest,
    TokenResponse,
    UserRead,
    UserUpdate,
)
from app.users.service import (
    access_token,
    authenticate,
    find_by_email,
    register,
    reset_password,
    reset_token,
    verification_token,
)

router = APIRouter(tags=["authentication", "users"])

GENERIC_EMAIL_RESPONSE = {"message": "If the account exists, we sent an email with instructions."}


def token_user(db: Session, token: str, purpose: str) -> tuple[User, dict] | None:
    payload = decode_token(token, purpose)
    try:
        user = db.get(User, UUID(payload["sub"])) if payload else None
    except (KeyError, ValueError):
        user = None
    return (user, payload) if user and user.is_active else None


def send_verification(background: BackgroundTasks, response: Response, user: User) -> None:
    token = verification_token(user)
    queue(background, verification_email(user, token))
    if get_settings().exposes_demo_tokens:
        response.headers["X-Demo-Verification-Token"] = token


@router.post("/auth/register", response_model=UserRead, status_code=201)
async def register_user(
    data: RegisterRequest,
    response: Response,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    try:
        user = register(db, data)
    except ValueError as error:
        raise HTTPException(409, detail="An account with this email already exists") from error
    send_verification(background, response, user)
    return user


@router.post("/auth/verify-email", response_model=UserRead)
def verify_email(data: TokenRequest, db: Session = Depends(get_db)):
    found = token_user(db, data.token, "verify_email")
    if not found or found[1].get("email") != found[0].email:
        raise HTTPException(400, detail="This verification link is invalid or has expired")
    user = found[0]
    if not user.email_verified:
        user.email_verified = True
        db.commit()
        db.refresh(user)
    return user


@router.post("/auth/resend-verification", status_code=202)
def resend_verification(
    data: EmailRequest,
    response: Response,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    user = find_by_email(db, data.email)
    if user and user.is_active and not user.email_verified:
        send_verification(background, response, user)
    return GENERIC_EMAIL_RESPONSE


@router.post("/auth/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate(db, data.email, data.password)
    if not user:
        raise HTTPException(401, detail="Invalid email or password")
    return TokenResponse(
        access_token=access_token(user),
        expires_in=get_settings().app_access_token_minutes * 60,
        user=user,
    )


@router.post("/auth/forgot-password", status_code=202)
def forgot_password(
    data: EmailRequest,
    response: Response,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    user = find_by_email(db, data.email)
    if user and user.is_active:
        token = reset_token(user)
        queue(background, password_reset_email(user, token))
        if get_settings().exposes_demo_tokens:
            response.headers["X-Demo-Reset-Token"] = token
    # Identical response either way, so the endpoint cannot be used to discover accounts.
    return GENERIC_EMAIL_RESPONSE


@router.post("/auth/reset-password", status_code=204)
def reset_password_confirm(
    data: PasswordResetConfirm, background: BackgroundTasks, db: Session = Depends(get_db)
):
    found = token_user(db, data.token, "reset_password")
    if not found or found[1].get("pwd") != password_fingerprint(found[0].password_hash):
        raise HTTPException(400, detail="This reset link is invalid, expired, or already used")
    user = found[0]
    reset_password(user, data.password)
    db.commit()
    queue(background, password_changed_email(user))


@router.get("/users/me", response_model=UserRead)
def read_me(user: User = Depends(current_user)):
    return user


@router.patch("/users/me", response_model=UserRead)
def update_me(data: UserUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user


@router.get("/notifications")
def notifications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return list(
        db.scalars(
            select(Notification)
            .where(Notification.user_id == user.id)
            .order_by(Notification.created_at.desc())
        ).all()
    )


@router.get("/notifications/unread-count")
def unread_count(user: User = Depends(current_user), db: Session = Depends(get_db)):
    from sqlalchemy import func

    return {
        "count": db.scalar(
            select(func.count(Notification.id)).where(
                Notification.user_id == user.id, Notification.is_read.is_(False)
            )
        )
        or 0
    }


@router.post("/notifications/{notification_id}/read", status_code=204)
def read_notification(
    notification_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    item = db.get(Notification, notification_id)
    if not item or item.user_id != user.id:
        raise HTTPException(404, detail="Notification not found")
    item.is_read = True
    db.commit()
