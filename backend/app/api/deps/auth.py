from datetime import datetime, timezone

import jwt
from fastapi import Depends, Header, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.error_codes import (
    ADMIN_PERMISSION_REQUIRED,
    AUTH_HEADER_MISSING,
    AUTH_REQUIRED,
    BOT_API_KEY_INVALID,
    BOT_DISABLED,
    DEMO_USER_NOT_FOUND,
    INVALID_AUTH_TOKEN,
)
from app.core.errors import api_error
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.bot import Bot
from app.models.user import User


def resolve_demo_user(db: Session, username: str | None) -> User:
    if not username:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=AUTH_HEADER_MISSING,
            message="Missing X-Demo-User header.",
        )

    user = db.scalar(
        select(User).where(User.username == username, User.status == "active")
    )
    if not user:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=DEMO_USER_NOT_FOUND,
            message=f"Demo user '{username}' not found or inactive.",
        )
    return user


def get_current_demo_user(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_demo_user: str | None = Header(default=None, alias="X-Demo-User"),
) -> User:
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise api_error(
                status_code=status.HTTP_401_UNAUTHORIZED,
                code=INVALID_AUTH_TOKEN,
                message="Invalid Authorization header.",
            )

        settings = get_settings()
        try:
            payload = decode_access_token(
                token=token, secret_key=settings.auth_secret_key
            )
        except jwt.PyJWTError:
            raise api_error(
                status_code=status.HTTP_401_UNAUTHORIZED,
                code=INVALID_AUTH_TOKEN,
                message="Invalid or expired access token.",
            )

        user_id = payload.get("uid")
        if not isinstance(user_id, int):
            raise api_error(
                status_code=status.HTTP_401_UNAUTHORIZED,
                code=INVALID_AUTH_TOKEN,
                message="Invalid token payload.",
            )

        user = db.get(User, user_id)
        if not user or user.status != "active":
            raise api_error(
                status_code=status.HTTP_401_UNAUTHORIZED,
                code=INVALID_AUTH_TOKEN,
                message="Token user not found or inactive.",
            )
        if user.user_type == "bot":
            raise api_error(
                status_code=status.HTTP_403_FORBIDDEN,
                code=INVALID_AUTH_TOKEN,
                message="Bot accounts must use X-Api-Key, not JWT.",
            )
        return user

    if x_demo_user:
        return resolve_demo_user(db, x_demo_user)

    raise api_error(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code=AUTH_REQUIRED,
        message="Authentication required.",
    )


def require_admin_user(current_user: User = Depends(get_current_demo_user)) -> User:
    if current_user.user_type != "admin":
        raise api_error(
            status_code=status.HTTP_403_FORBIDDEN,
            code=ADMIN_PERMISSION_REQUIRED,
            message="Admin permission required.",
        )
    return current_user


def get_bot_user(
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    db: Session = Depends(get_db),
) -> tuple[User, Bot]:
    if not x_api_key:
        raise api_error(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=BOT_API_KEY_INVALID,
            message="X-Api-Key header is required.",
        )
    bot = db.scalar(select(Bot).where(Bot.api_key == x_api_key))
    if not bot:
        raise api_error(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=BOT_API_KEY_INVALID,
            message="Invalid API key.",
        )
    if not bot.is_enabled:
        raise api_error(
            status_code=status.HTTP_403_FORBIDDEN,
            code=BOT_DISABLED,
            message="Bot is disabled. Enable it in your account settings.",
        )
    user = db.get(User, bot.user_id)
    if not user or user.status != "active":
        raise api_error(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=BOT_API_KEY_INVALID,
            message="Associated user not found or inactive.",
        )
    bot.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return user, bot
