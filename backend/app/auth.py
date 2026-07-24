"""Bearer-token auth, mirroring ai-agent-db's constant-time compare.

HTTP routes use the ``require_api_token`` dependency. Browser WebSockets cannot
set custom headers, so the WS handshake authenticates via a ``?token=`` query
param checked with ``verify_token`` (same constant-time comparison).
"""
from __future__ import annotations

import secrets

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings


def verify_token(token: str | None, settings: Settings) -> bool:
    """Constant-time comparison of a presented token against the configured one."""
    if not token:
        return False
    return secrets.compare_digest(token, settings.voice_api_token)


def require_api_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """FastAPI dependency for HTTP routes. Expects ``Authorization: Bearer <token>``."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not verify_token(token, settings):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
