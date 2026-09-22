import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from loguru import logger

from app.config import settings

# HTTP Bearer scheme
bearer_scheme = HTTPBearer(auto_error=False)


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Create a signed JWT access token with expiration time."""
    to_encode = data.copy()
    now = datetime.now(UTC)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.access_token_expire_minutes)

    to_encode.update({"exp": expire, "iat": now})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate a signed JWT token."""
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
        return payload
    except JWTError as e:
        logger.warning(f"JWT Validierungsfehler: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiges oder abgelaufenes Token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


def verify_credentials(username: str, password: str) -> bool:
    """Verify submitted credentials against configured API user and password."""
    user_ok = secrets.compare_digest(username, settings.api_username)
    pass_ok = secrets.compare_digest(password, settings.api_password)
    return user_ok and pass_ok


async def get_current_user(
    auth: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    token_query: str | None = Query(default=None, alias="token"),
) -> dict[str, Any]:
    """FastAPI dependency to extract and authenticate the current user from Bearer header or query parameter."""
    token = None
    if auth and auth.credentials:
        token = auth.credentials
    elif token_query:
        token = token_query

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentifizierung erforderlich. Kein Token bereitgestellt.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(token)
    username: str | None = payload.get("sub") or payload.get("username")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiges Token-Format (fehlender Benutzername).",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {"username": username, "role": payload.get("role", "admin")}
