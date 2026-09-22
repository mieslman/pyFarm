from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.core.auth import create_access_token, verify_credentials

auth_router = APIRouter(tags=["Authentication"])


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    accessToken: str  # Legacy Node.js compatibility
    expires_in: int = 86400


@auth_router.post("/login", response_model=TokenResponse)
async def login(credentials: LoginRequest):
    """Authenticate with username and password to receive a JWT access token."""
    if not verify_credentials(credentials.username, credentials.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiger Benutzername oder Passwort.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token({"sub": credentials.username, "role": "admin"})
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        accessToken=token,
        expires_in=86400,
    )
