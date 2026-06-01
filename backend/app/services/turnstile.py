"""Cloudflare Turnstile verification helper.

Posts a user-supplied Turnstile token to Cloudflare's siteverify endpoint
and returns whether the challenge passed.

Behaviour summary
-----------------
- ``await verify_turnstile(token, client_ip)`` returns ``True`` only when
  Cloudflare's siteverify endpoint responds with HTTP 2xx and a JSON body
  containing ``{"success": true}``.
- Network errors, timeouts, non-2xx responses, malformed JSON, and
  ``{"success": false}`` all resolve to ``False``. Failures are logged at
  WARNING level; when Cloudflare returns ``error_codes`` they are included
  in the log line.
- When ``TURNSTILE_SECRET_KEY`` is empty (local dev / test environments
  that have not provisioned a Turnstile widget) the helper short-circuits
  to ``True``. A single INFO-level warning is emitted per process so the
  bypass is visible in logs without being noisy.
- The module-level constant ``TEST_TOKEN_BYPASS`` ("TURNSTILE_TEST_BYPASS")
  short-circuits to ``True`` regardless of configuration. Use this in
  integration tests where a real Turnstile token is unavailable but you
  still want the secret-configured code path to execute.
"""

from __future__ import annotations

import logging

import httpx

from app.settings import get_settings

logger = logging.getLogger(__name__)

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TEST_TOKEN_BYPASS = "TURNSTILE_TEST_BYPASS"

_unconfigured_warning_emitted = False


def _emit_unconfigured_warning_once() -> None:
    global _unconfigured_warning_emitted
    if _unconfigured_warning_emitted:
        return
    _unconfigured_warning_emitted = True
    logger.info(
        "TURNSTILE_SECRET_KEY is not configured; Turnstile verification "
        "is bypassed for this process."
    )


async def verify_turnstile(token: str, client_ip: str) -> bool:
    """Verify a Turnstile token with Cloudflare.

    Returns ``True`` on a successful verification, ``False`` otherwise.
    Never raises.
    """
    if token == TEST_TOKEN_BYPASS:
        return True

    secret = get_settings().turnstile_secret_key
    if not secret:
        _emit_unconfigured_warning_once()
        return True

    if not token:
        logger.warning("Turnstile verification rejected: empty token")
        return False

    payload = {
        "secret": secret,
        "response": token,
        "remoteip": client_ip,
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(TURNSTILE_VERIFY_URL, data=payload)
    except httpx.TimeoutException:
        logger.warning("Turnstile verification timed out after 5s")
        return False
    except httpx.HTTPError as exc:
        logger.warning("Turnstile verification HTTP error: %s", exc)
        return False

    if response.status_code // 100 != 2:
        logger.warning(
            "Turnstile verification returned HTTP %s", response.status_code
        )
        return False

    try:
        body = response.json()
    except ValueError:
        logger.warning("Turnstile verification returned non-JSON body")
        return False

    if not isinstance(body, dict):
        logger.warning("Turnstile verification returned unexpected body type")
        return False

    if body.get("success") is True:
        return True

    error_codes = body.get("error_codes")
    if error_codes:
        logger.warning(
            "Turnstile verification failed: error_codes=%s", error_codes
        )
    else:
        logger.warning("Turnstile verification failed without error_codes")
    return False
