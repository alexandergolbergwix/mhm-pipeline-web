"""Rate-limiting middleware for the MHM Pipeline web API.

Built on `slowapi` (a Starlette/FastAPI port of Flask-Limiter). The limiter
keys requests by the real client IP, honouring the standard
``X-Forwarded-For`` header that Heroku's router populates so that the limit
is applied to the originating client rather than the Heroku proxy hop.

Storage
-------

The default storage backend is in-process memory — sufficient for the
current single-dyno Heroku deployment. If/when the app scales to multiple
dynos, the counters must move to a shared store so every dyno sees the
same totals. To migrate:

1. Provision Heroku Redis Mini (or any Redis compatible URL):
   ``heroku addons:create heroku-redis:mini``.
2. Set the storage URI env var:
   ``heroku config:set RATELIMIT_STORAGE_URI=redis://...``.
3. slowapi reads ``RATELIMIT_STORAGE_URI`` automatically; no code change
   is required.

Wiring
------

``app/main.py`` is responsible for installing the limiter on the FastAPI
app and registering the exception handler::

    from app.middleware.rate_limit import (
        limiter,
        RateLimitExceeded,
        _rate_limit_exceeded_handler,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

Individual routes opt in with the ``@limiter.limit("N/period")`` decorator.
"""

from __future__ import annotations

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

__all__ = [
    "limiter",
    "RateLimitExceeded",
    "_rate_limit_exceeded_handler",
]


def _real_client_ip(request: Request) -> str:
    """Return the originating client IP for rate-limit keying.

    Heroku's router forwards the upstream client IP in ``X-Forwarded-For``
    as a comma-separated list (left-most entry is the original client).
    Falls back to the direct socket peer, then to ``"0.0.0.0"`` if even
    that is unavailable (e.g. in unit tests with a synthetic request).
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "0.0.0.0"


limiter = Limiter(key_func=_real_client_ip)
