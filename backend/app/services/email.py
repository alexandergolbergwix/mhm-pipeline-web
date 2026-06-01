"""Resend transactional-email wrapper.

A small, fully-typed sender used by the public "request access" flow
and by the admin approval / denial flow. Every method:

* Goes through :class:`EmailThrottle` first — per-recipient rate
  limit (1/60 s, 5/24 h) backed by Postgres so an attacker can't
  email-bomb an inbox through our SMTP relay.
* Lazy-imports the third-party ``resend`` SDK inside the method
  body so an unconfigured environment (no ``RESEND_API_KEY``,
  package not yet installed during a green-field test run) does
  not break import of the FastAPI app.
* Never raises into the caller's request path. Failures are
  logged and surface as ``False``. Registration must succeed even
  if delivery is degraded — the admin queue will still show the
  request.

If ``RESEND_API_KEY`` is empty, the sender enters **log-only mode**:
every ``send_*`` writes a structured log line describing the email
it *would* have sent and returns ``False``. This keeps local
development pleasant and CI runs cheap.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.email_throttle import EmailThrottle
from app.settings import get_settings

logger = logging.getLogger(__name__)

_FROM_DISPLAY_NAME = "MHM Pipeline (Mapping Hebrew Manuscripts)"


def _from_header(from_email: str) -> str:
    """Render an RFC-5322 ``Display Name <addr@host>`` From header."""
    if not from_email:
        return _FROM_DISPLAY_NAME
    return f"{_FROM_DISPLAY_NAME} <{from_email}>"


class EmailSender:
    """Resend transactional-email wrapper.

    Instantiated once via :func:`get_email_sender` (``@lru_cache``).
    All methods are coroutines so the router can ``await`` them in a
    uniform style; the underlying Resend SDK call is sync, wrapped in
    ``try/except`` so a delivery failure never bubbles up.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key: str = getattr(settings, "resend_api_key", "") or ""
        self._from_email: str = (
            getattr(settings, "resend_from_email", "") or ""
        )
        self._admin_email: str = (
            getattr(settings, "admin_notification_email", "") or ""
        )
        self._log_only: bool = not self._api_key
        if self._log_only:
            logger.info(
                "EmailSender in log-only mode (RESEND_API_KEY unset); "
                "emails will be logged but not delivered.",
            )

    # ── public API ────────────────────────────────────────────────────

    async def send_request_confirmation(
        self,
        db: AsyncSession,
        to: str,
        confirm_url: str,
    ) -> bool:
        """Double-opt-in confirmation email for a new access request."""
        subject = "Confirm your MHM Pipeline access request"
        html = (
            "<p>Thanks for requesting access to "
            "<strong>MHM Pipeline (Mapping Hebrew Manuscripts)</strong>.</p>"
            "<p>To confirm this is really your email address, "
            f'please click the link below within 24 hours:</p>'
            f'<p><a href="{confirm_url}">{confirm_url}</a></p>'
            "<p>After you confirm, an administrator will review your "
            "request and you'll hear back by email.</p>"
            "<p>If you didn't request access, you can ignore this "
            "message — no account will be created.</p>"
        )
        text = (
            "Thanks for requesting access to MHM Pipeline "
            "(Mapping Hebrew Manuscripts).\n\n"
            "To confirm this is really your email address, please open "
            f"the following link within 24 hours:\n\n{confirm_url}\n\n"
            "After you confirm, an administrator will review your "
            "request and you'll hear back by email.\n\n"
            "If you didn't request access, you can ignore this "
            "message — no account will be created.\n"
        )
        return await self._send(db, to=to, subject=subject, html=html, text=text)

    async def send_admin_notification(
        self,
        db: AsyncSession,
        to_admin: str,
        requester_name: str,
        requester_email: str,
        affiliation: str,
        justification: str,
        approve_url: str,
        deny_url: str,
    ) -> bool:
        """Notify the admin that a confirmed request is awaiting review."""
        subject = "New MHM Pipeline access request awaiting review"
        # Defensive escaping for inline HTML interpolation.
        safe_name = _html_escape(requester_name)
        safe_email = _html_escape(requester_email)
        safe_affiliation = _html_escape(affiliation)
        safe_justification = _html_escape(justification)
        html = (
            "<p>A new request to join "
            "<strong>MHM Pipeline (Mapping Hebrew Manuscripts)</strong> "
            "has been confirmed and is awaiting your decision.</p>"
            "<table cellpadding='6' style='border-collapse:collapse'>"
            f"<tr><td><strong>Name</strong></td><td>{safe_name}</td></tr>"
            f"<tr><td><strong>Email</strong></td><td>{safe_email}</td></tr>"
            f"<tr><td><strong>Affiliation</strong></td>"
            f"<td>{safe_affiliation}</td></tr>"
            f"<tr><td><strong>Justification</strong></td>"
            f"<td>{safe_justification}</td></tr>"
            "</table>"
            "<p style='margin-top:1em'>"
            f'<a href="{approve_url}">Approve</a> &nbsp;|&nbsp; '
            f'<a href="{deny_url}">Deny</a>'
            "</p>"
            "<p>These links require you to be signed in as an admin. "
            "If you're not signed in you'll be redirected through the "
            "login page first.</p>"
        )
        text = (
            "A new request to join MHM Pipeline (Mapping Hebrew "
            "Manuscripts) has been confirmed and is awaiting your "
            "decision.\n\n"
            f"Name:          {requester_name}\n"
            f"Email:         {requester_email}\n"
            f"Affiliation:   {affiliation}\n"
            f"Justification: {justification}\n\n"
            f"Approve: {approve_url}\n"
            f"Deny:    {deny_url}\n\n"
            "These links require you to be signed in as an admin. If "
            "you're not signed in you'll be redirected through the "
            "login page first.\n"
        )
        return await self._send(db, to=to_admin, subject=subject, html=html, text=text)

    async def send_set_password(
        self,
        db: AsyncSession,
        to: str,
        set_password_url: str,
        expires_hours: int,
    ) -> bool:
        """Tell an approved requester to set their password."""
        subject = "Welcome to MHM Pipeline — set your password"
        html = (
            "<p>Your request to join "
            "<strong>MHM Pipeline (Mapping Hebrew Manuscripts)</strong> "
            "has been approved.</p>"
            "<p>Set your password by clicking the link below within "
            f"{expires_hours} hours:</p>"
            f'<p><a href="{set_password_url}">{set_password_url}</a></p>'
            "<p>Once you've set a password you can sign in at the same "
            "URL with your email and the password you just chose.</p>"
        )
        text = (
            "Your request to join MHM Pipeline (Mapping Hebrew "
            "Manuscripts) has been approved.\n\n"
            "Set your password by opening the following link within "
            f"{expires_hours} hours:\n\n{set_password_url}\n\n"
            "Once you've set a password you can sign in at the same "
            "URL with your email and the password you just chose.\n"
        )
        return await self._send(db, to=to, subject=subject, html=html, text=text)

    async def send_existing_account_notice(
        self,
        db: AsyncSession,
        to: str,
    ) -> bool:
        """Out-of-band notice: 'someone tried to register with your email'.

        Sent only to addresses that already correspond to a real user
        account. The public ``POST /api/access-request`` response is
        deliberately identical regardless of whether the email exists
        (OWASP strict enumeration resistance) — this email is the
        legitimate-user-side notification of the attempt.
        """
        subject = "Someone tried to request access with your email"
        html = (
            "<p>Hello,</p>"
            "<p>Someone — possibly you — tried to request access to "
            "<strong>MHM Pipeline (Mapping Hebrew Manuscripts)</strong> "
            "using this email address. You already have an account, so "
            "no new request was created.</p>"
            "<p>If this was you, you can sign in normally with your "
            "existing password. If you've forgotten it, use the "
            '"forgot password" link on the sign-in page.</p>'
            "<p>If this wasn't you, no action is required — your "
            "account is unaffected — but you may want to review your "
            "recent sign-in activity.</p>"
        )
        text = (
            "Hello,\n\n"
            "Someone — possibly you — tried to request access to MHM "
            "Pipeline (Mapping Hebrew Manuscripts) using this email "
            "address. You already have an account, so no new request "
            "was created.\n\n"
            "If this was you, you can sign in normally with your "
            "existing password. If you've forgotten it, use the "
            "'forgot password' link on the sign-in page.\n\n"
            "If this wasn't you, no action is required — your account "
            "is unaffected — but you may want to review your recent "
            "sign-in activity.\n"
        )
        return await self._send(db, to=to, subject=subject, html=html, text=text)

    async def send_denial_notice(
        self,
        db: AsyncSession,
        to: str,
        reason: str,
    ) -> bool:
        """Tell a requester their access request was denied."""
        subject = "Your MHM Pipeline access request"
        safe_reason = _html_escape(reason) if reason else ""
        reason_block_html = (
            f"<p><strong>Reason given:</strong> {safe_reason}</p>"
            if reason
            else ""
        )
        reason_block_text = (
            f"Reason given: {reason}\n\n" if reason else ""
        )
        html = (
            "<p>Thank you for your interest in "
            "<strong>MHM Pipeline (Mapping Hebrew Manuscripts)</strong>.</p>"
            "<p>After review, we're unable to grant access at this "
            "time.</p>"
            f"{reason_block_html}"
            "<p>If you believe this was decided in error, you may "
            "reply to this email and we'll take another look.</p>"
        )
        text = (
            "Thank you for your interest in MHM Pipeline (Mapping "
            "Hebrew Manuscripts).\n\n"
            "After review, we're unable to grant access at this time.\n\n"
            f"{reason_block_text}"
            "If you believe this was decided in error, you may reply "
            "to this email and we'll take another look.\n"
        )
        return await self._send(db, to=to, subject=subject, html=html, text=text)

    # ── internals ─────────────────────────────────────────────────────

    async def _send(
        self,
        db: AsyncSession,
        *,
        to: str,
        subject: str,
        html: str,
        text: str,
    ) -> bool:
        """Throttle-check → log-only or call Resend.

        Returns True on a successful Resend ``send`` call, False if
        the email was throttled, suppressed (log-only mode), or the
        SDK raised. Never raises.
        """
        if not to:
            logger.warning("EmailSender._send called with empty recipient")
            return False

        try:
            allowed = await EmailThrottle.allow(db, to)
        except Exception:  # noqa: BLE001 — throttle must not break delivery
            logger.exception(
                "EmailThrottle.allow raised for recipient; "
                "failing closed (not sending)",
            )
            return False

        if not allowed:
            logger.info(
                "email throttled subject=%r recipient_redacted=%s",
                subject,
                _redact_email(to),
            )
            return False

        if self._log_only:
            logger.info(
                "[log-only] email subject=%r to=%s\n--- text ---\n%s",
                subject,
                _redact_email(to),
                text,
            )
            return False

        try:
            import resend  # noqa: PLC0415  — lazy per CLAUDE.md rule 2 spirit

            resend.api_key = self._api_key
            params = {
                "from": _from_header(self._from_email),
                "to": [to],
                "subject": subject,
                "html": html,
                "text": text,
            }
            # The Resend SDK's send is synchronous; we accept that and
            # do not block long enough to need a thread executor.
            resend.Emails.send(params)
        except Exception:  # noqa: BLE001 — never raise into request path
            logger.exception(
                "resend send failed subject=%r recipient_redacted=%s",
                subject,
                _redact_email(to),
            )
            return False

        logger.info(
            "email sent subject=%r recipient_redacted=%s",
            subject,
            _redact_email(to),
        )
        return True


def _html_escape(value: str) -> str:
    """Minimal HTML escape for interpolation into our inline templates."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _redact_email(addr: str) -> str:
    """Return a log-safe redaction of ``user@host`` → ``u***@host``."""
    if "@" not in addr:
        return "***"
    local, _, host = addr.partition("@")
    if not local:
        return f"***@{host}"
    return f"{local[0]}***@{host}"


@lru_cache(maxsize=1)
def get_email_sender() -> EmailSender:
    """Singleton accessor used by routers and the app startup hook."""
    return EmailSender()
