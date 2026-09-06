from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.dependencies import current_user
from app.core.security import create_token, decode_token, hash_password
from app.users.models import Notification, User
from app.users.schemas import (
    LoginRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RegisterRequest,
    TokenRequest,
    TokenResponse,
    UserRead,
    UserUpdate,
)
from app.users.service import authenticate, register

router = APIRouter(tags=["authentication", "users"])


@router.post("/auth/register", response_model=UserRead, status_code=201)
async def register_user(data: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    try:
        user, verification_token = register(db, data)
    except ValueError as error:
        raise HTTPException(409, detail=str(error)) from error
    response.headers["X-Demo-Verification-Token"] = verification_token
    return user


@router.post("/auth/verify-email", response_model=UserRead)
def verify_email(data: TokenRequest, db: Session = Depends(get_db)):
    payload = decode_token(data.token, "verify_email")
    user = db.get(User, UUID(payload["sub"])) if payload else None
    if not user:
        raise HTTPException(400, detail="Invalid or expired verification token")
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return user


@router.post("/auth/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate(db, data.email, data.password)
    if not user:
        raise HTTPException(401, detail="Invalid email or password")
    settings = get_settings()
    return TokenResponse(
        access_token=create_token(str(user.id)),
        expires_in=settings.app_access_token_minutes * 60,
        user=user,
    )


@router.post("/auth/forgot-password", status_code=202)
def forgot_password(data: PasswordResetRequest, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    if user:
        response.headers["X-Demo-Reset-Token"] = create_token(str(user.id), "reset_password", 30)
    return {"message": "If the account exists, password reset instructions were created."}


@router.post("/auth/reset-password", status_code=204)
def reset_password(data: PasswordResetConfirm, db: Session = Depends(get_db)):
    payload = decode_token(data.token, "reset_password")
    user = db.get(User, UUID(payload["sub"])) if payload else None
    if not user:
        raise HTTPException(400, detail="Invalid or expired reset token")
    user.password_hash = hash_password(data.password)
    db.commit()


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
