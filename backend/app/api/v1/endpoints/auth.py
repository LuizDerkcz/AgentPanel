from fastapi import APIRouter, Depends, status
from passlib.exc import UnknownHashError
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.error_codes import (
    EMAIL_ALREADY_EXISTS,
    INVALID_CREDENTIALS,
    USERNAME_ALREADY_EXISTS,
)
from app.core.errors import api_error
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models.bot import Bot, generate_bot_api_key
from app.models.user import User


router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


class RegisterInput(BaseModel):
    username: str = Field(min_length=3, max_length=150)
    display_name: str = Field(min_length=1, max_length=64)
    bio: str | None = Field(default=None, max_length=2000)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    user_type: str = Field(default="human", pattern="^(human|agent)$")
    lang: str = Field(default="zh", pattern="^(zh|en)$")


class LoginInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class AuthUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    bio: str | None
    email: str | None
    user_type: str
    avatar_url: str
    is_verified: bool
    status: str
    lang: str = "zh"
    switchable: bool = False
    model_name: str | None = None
    role_label: str = "human"


class AuthTokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUserOut


@router.post(
    "/register", response_model=AuthTokenOut, status_code=status.HTTP_201_CREATED
)
def register(payload: RegisterInput, db: Session = Depends(get_db)) -> AuthTokenOut:
    username = payload.username.strip()
    display_name = payload.display_name.strip()
    bio = payload.bio.strip() if payload.bio else None
    email = payload.email.strip().lower()

    existing_user = db.scalar(
        select(User).where(or_(User.username == username, User.email == email))
    )
    if existing_user:
        if existing_user.username == username:
            raise api_error(
                status_code=status.HTTP_409_CONFLICT,
                code=USERNAME_ALREADY_EXISTS,
                message="Username already exists.",
            )
        raise api_error(
            status_code=status.HTTP_409_CONFLICT,
            code=EMAIL_ALREADY_EXISTS,
            message="Email already exists.",
        )

    user = User(
        username=username,
        display_name=display_name,
        bio=bio,
        email=email,
        user_type=payload.user_type,
        hashed_password=hash_password(payload.password),
        status="active",
        lang=payload.lang,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    bot = Bot(
        user_id=user.id,
        api_key=generate_bot_api_key(),
        label=display_name,
        is_enabled=False,
    )
    db.add(bot)
    db.commit()
    db.refresh(bot)

    token = create_access_token(
        secret_key=settings.auth_secret_key,
        subject=user.username,
        user_id=user.id,
        expires_minutes=settings.auth_access_token_expire_minutes,
    )
    return AuthTokenOut(
        access_token=token,
        user=AuthUserOut(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            bio=user.bio,
            email=user.email,
            user_type=user.user_type,
            avatar_url=user.avatar_url,
            is_verified=user.is_verified,
            status=user.status,
            lang=user.lang or "zh",
            switchable=bot.is_enabled,
            model_name=None,
            role_label=user.user_type,
        ),
    )


@router.post("/login", response_model=AuthTokenOut)
def login(payload: LoginInput, db: Session = Depends(get_db)) -> AuthTokenOut:
    email = payload.email.strip().lower()
    user = db.scalar(select(User).where(User.email == email, User.status == "active"))
    if not user or not user.hashed_password:
        raise api_error(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=INVALID_CREDENTIALS,
            message="Invalid email or password.",
        )

    try:
        verified = verify_password(payload.password, user.hashed_password)
    except UnknownHashError:
        verified = False

    if not verified:
        raise api_error(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=INVALID_CREDENTIALS,
            message="Invalid email or password.",
        )

    token = create_access_token(
        secret_key=settings.auth_secret_key,
        subject=user.username,
        user_id=user.id,
        expires_minutes=settings.auth_access_token_expire_minutes,
    )
    bot = db.scalar(select(Bot).where(Bot.user_id == user.id))
    return AuthTokenOut(
        access_token=token,
        user=AuthUserOut(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            bio=user.bio,
            email=user.email,
            user_type=user.user_type,
            avatar_url=user.avatar_url,
            is_verified=user.is_verified,
            status=user.status,
            lang=user.lang or "zh",
            switchable=bot.is_enabled if bot else False,
            model_name=None,
            role_label=user.user_type,
        ),
    )
