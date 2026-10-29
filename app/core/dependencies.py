from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token, password_fingerprint
from app.users.models import User, UserRole

bearer = HTTPBearer(auto_error=False)


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    payload = decode_token(credentials.credentials) if credentials else None
    try:
        user_id = UUID(payload["sub"]) if payload else None
    except (ValueError, KeyError):
        user_id = None
    user = db.get(User, user_id) if user_id else None
    # Login tokens carry a password fingerprint, so those issued before the latest password
    # change are rejected. Signed internal tokens without the claim are accepted as before.
    if (
        not user
        or not user.is_active
        or ("pwd" in payload and payload["pwd"] != password_fingerprint(user.password_hash))
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication"
        )
    return user


def require_role(*roles: UserRole):
    async def dependency(user: User = Depends(current_user)) -> User:
        if not set(roles).intersection(user.roles):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return dependency


async def optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
):
    if credentials is None:
        return None
    return await current_user(credentials, db)
