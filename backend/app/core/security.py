from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from passlib.context import CryptContext


ALGORITHM = "HS256"
_password_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(password: str) -> str:
    return _password_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return _password_context.verify(password, hashed_password)


def is_supported_password_hash(hashed_password: str | None) -> bool:
    if not hashed_password:
        return False
    return _password_context.identify(hashed_password) is not None


def create_access_token(
    *,
    secret_key: str,
    subject: str,
    user_id: int,
    expires_minutes: int,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "uid": user_id,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
        "type": "access",
    }
    return jwt.encode(payload, secret_key, algorithm=ALGORITHM)


def decode_access_token(*, token: str, secret_key: str) -> dict[str, Any]:
    return jwt.decode(token, secret_key, algorithms=[ALGORITHM])
