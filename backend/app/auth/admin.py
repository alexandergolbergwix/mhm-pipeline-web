"""Admin-only FastAPI dependency."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status

from app.auth.session import AuthContext, current_auth
from app.models.user import ROLE_ADMIN


def require_admin(auth: AuthContext = Depends(current_auth)) -> AuthContext:
    if auth.user.role != ROLE_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required",
        )
    return auth
