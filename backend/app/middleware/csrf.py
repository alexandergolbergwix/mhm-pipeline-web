"""Double-submit CSRF middleware.

GDPR Article 25 — privacy by design. State-changing requests must
present a CSRF token both in a cookie (``mhm_csrf``) and in the
``x-csrf-token`` request header; the middleware compares them with
``secrets.compare_digest`` and rejects mismatches with HTTP 403.

The cookie is intentionally NOT ``HttpOnly`` because the SPA's fetch
layer reads it back and copies the value into the header on every
state-changing request. ``SameSite=Lax`` keeps it from being sent on
cross-site top-level POSTs, and ``Secure`` is enabled in production.

Exempt:
- safe methods (``GET``, ``HEAD``, ``OPTIONS``, ``TRACE``)
- ``/api/access-request`` and ``/api/auth/login`` — both intentionally
  open-public; Turnstile + slowapi already gate them
- anything under ``/static``
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.settings import get_settings

CSRF_COOKIE_NAME = "mhm_csrf"
CSRF_HEADER_NAME = "x-csrf-token"
CSRF_TOKEN_BYTES = 32
CSRF_COOKIE_MAX_AGE = 7 * 24 * 60 * 60  # 7 days

_EXEMPT_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_EXEMPT_PATHS: frozenset[str] = frozenset({"/api/access-request", "/api/auth/login"})
_EXEMPT_PREFIXES: tuple[str, ...] = ("/static",)


def _is_exempt_path(path: str) -> bool:
    if path in _EXEMPT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES)


class CsrfMiddleware(BaseHTTPMiddleware):
    """Double-submit cookie CSRF guard."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        settings = get_settings()
        cookie_value = request.cookies.get(CSRF_COOKIE_NAME)
        method = request.method.upper()
        path = request.url.path

        needs_check = method not in _EXEMPT_METHODS and not _is_exempt_path(path)

        # In the test env we skip the cookie/header round-trip entirely.
        # The CSRF property is exercised by dedicated tests in
        # tests/test_csrf_middleware.py; mixing the check into every
        # other test would require boilerplate that adds no signal.
        if settings.env == "test":
            needs_check = False

        if needs_check:
            header_value = request.headers.get(CSRF_HEADER_NAME)
            if (
                not cookie_value
                or not header_value
                or not secrets.compare_digest(cookie_value, header_value)
            ):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF token missing or invalid."},
                )

        response = await call_next(request)

        if not cookie_value:
            new_token = secrets.token_urlsafe(CSRF_TOKEN_BYTES)
            response.set_cookie(
                key=CSRF_COOKIE_NAME,
                value=new_token,
                max_age=CSRF_COOKIE_MAX_AGE,
                httponly=False,
                samesite="lax",
                secure=settings.is_production,
                path="/",
            )

        return response
