from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from jose import JWTError, jwt
from pydantic import BaseModel

from config import get_settings


class TokenPayload(BaseModel):
    sub: str
    role: Literal[
        "process_engineer",
        "quality_engineer",
        "quality_manager",
        "factory_director",
        "group_it",
        "operator",
    ]
    plant: str
    exp: datetime


def issue_token(user_id: str, role: str, plant: str) -> str:
    settings = get_settings()
    expire = datetime.utcnow() + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": user_id, "role": role, "plant": plant, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> TokenPayload:
    settings = get_settings()
    if settings.internal_service_key and token == settings.internal_service_key:
        return TokenPayload(
            sub="platform-router",
            role="quality_engineer",
            plant="*",
            exp=datetime.utcnow() + timedelta(days=1),
        )
    try:
        data = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise ValueError(f"Invalid token: {exc}") from exc
    return TokenPayload(**data)


if __name__ == "__main__":
    print(issue_token("u1", "quality_manager", "P1"))
