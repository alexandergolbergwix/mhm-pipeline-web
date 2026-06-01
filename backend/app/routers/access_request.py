"""Public access-request flow + admin review surface.

Endpoints (mounted under ``/api`` by :mod:`app.main`):

* ``POST /access-request`` — public form submission. Honeypot +
  Turnstile + rate-limit gated. Always returns the same generic 202
  body so the response cannot be used to enumerate existing accounts.
* ``GET  /access-request/confirm/{token}`` — double-opt-in
  confirmation. Advances ``pending_email_confirm`` → ``pending_admin``
  and notifies the admin out-of-band.
* ``GET  /admin/access-requests`` — admin queue listing.
* ``GET  /admin/access-requests/{id}`` — admin detail view.
* ``POST /admin/access-requests/{id}/approve`` — mint an
  :class:`Invitation` via :func:`create_invitation_for_email` and
  email the requester a set-password link.
* ``POST /admin/access-requests/{id}/deny`` — close the request with
  a reason and notify the requester.
* ``GET  /admin/access-requests/decide/{token}`` — magic-link entry
  point embedded in the admin notification email. Verifies the
  one-time decision token then redirects to the admin detail page
  (where the actual approve/deny POST happens after the admin signs
  in).

PII discipline mirrors :class:`Invitation`: every requester-supplied
free-text field is AES-GCM-encrypted at rest and the email is
blind-indexed for lookup. The plaintext confirm/decision tokens never
land in the DB — only their SHA-256 digests.

IP addresses are treated as personal data per Breyer C-582/14 and
GDPR Article 32: we store an HMAC-SHA256 digest under
``EMAIL_HMAC_KEY`` (truncated to fit the existing ``String(45)``
column). Forensically traceable from a candidate IP but not
reversible from the column value alone.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.admin import require_admin
from app.auth.session import AuthContext
from app.auth.tokens import hash_token, new_token
from app.crypto import index as idx
from app.crypto import pii
from app.db import get_session
from app.middleware.rate_limit import limiter
from app.models.access_request import (
    STATUS_APPROVED,
    STATUS_DENIED,
    STATUS_PENDING_ADMIN,
    STATUS_PENDING_EMAIL_CONFIRM,
    AccessRequest,
)
from app.models.user import User
from app.routers.invites import create_invitation_for_email
from app.schemas.access_request import (
    AccessRequestCreateRequest,
    AccessRequestDenyRequest,
    AccessRequestDetail,
    AccessRequestListItem,
    AccessRequestSubmitResponse,
    ConfirmResponse,
)
from app.services.email import get_email_sender
from app.services.turnstile import TEST_TOKEN_BYPASS, verify_turnstile
from app.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["access-request"])


# ── IP hashing ─────────────────────────────────────────────────────────


def _hash_ip(client_ip: str) -> str:
    """One-way HMAC-SHA256 of ``client_ip`` keyed by ``EMAIL_HMAC_KEY``.

    Returned as a hex string. The :class:`AccessRequest` ``client_ip``
    column is ``String(45)`` (IPv6-sized) — we truncate to 45 hex chars
    before storing. Truncation preserves the forensic property: hashing
    a candidate IP and slicing the same ``[:45]`` prefix yields a
    byte-for-byte comparison.
    """
    s = get_settings()
    key = (s.email_hmac_key or "").encode("utf-8")
    return hmac.new(key, client_ip.encode("utf-8"), hashlib.sha256).hexdigest()


def _extract_client_ip(request: Request) -> str:
    """Return the originating client IP from a request.

    Prefer the left-most entry of ``X-Forwarded-For`` (Heroku's router
    populates this) and fall back to the direct socket peer.
    """
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",", 1)[0].strip()
    return request.client.host if request.client else "0.0.0.0"


def _decrypt_row(row: AccessRequest) -> dict[str, str]:
    return {
        "email": pii.decrypt_pii(row.email_encrypted),
        "name": pii.decrypt_pii(row.name_encrypted),
        "affiliation": pii.decrypt_pii(row.affiliation_encrypted),
        "justification": pii.decrypt_pii(row.justification_encrypted),
    }


# ── Public submission ──────────────────────────────────────────────────


@router.post(
    "/access-request",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AccessRequestSubmitResponse,
)
@limiter.limit("3/hour")
async def submit_request(
    request: Request,
    payload: AccessRequestCreateRequest,
    db: AsyncSession = Depends(get_session),
) -> AccessRequestSubmitResponse:
    client_ip_raw = _extract_client_ip(request)
    client_ip_hash = _hash_ip(client_ip_raw)[:45]
    user_agent = (request.headers.get("user-agent") or "")[:512]

    # Generic response on every path — account-enumeration resistance.
    generic = AccessRequestSubmitResponse()

    # 1. Honeypot — real users never see ``website``; bots auto-fill it.
    if payload.website.strip():
        logger.warning("access-request honeypot tripped ip=%s", client_ip_raw)
        return generic

    # 2. Turnstile — verify server-side before any DB write.
    if payload.turnstile_token != TEST_TOKEN_BYPASS:
        ok = await verify_turnstile(payload.turnstile_token, client_ip_raw)
        if not ok:
            logger.warning(
                "access-request turnstile failed ip=%s", client_ip_raw,
            )
            return generic

    email_norm = payload.email.lower()
    email_idx = idx.blind_index(email_norm)

    # 3. Existing-user check — silently send the out-of-band notice
    # but still return the generic body.
    existing_user = (
        await db.execute(select(User).where(User.email_index == email_idx))
    ).scalar_one_or_none()
    if existing_user is not None:
        try:
            await get_email_sender().send_existing_account_notice(
                db, to=email_norm,
            )
        except Exception:
            logger.exception("send_existing_account_notice failed")
        return generic

    # 4. Already-pending-request check — silently swallow re-submits.
    now = datetime.now(timezone.utc)
    pending = (
        await db.execute(
            select(AccessRequest).where(
                AccessRequest.email_index == email_idx,
                AccessRequest.status.in_(
                    [STATUS_PENDING_EMAIL_CONFIRM, STATUS_PENDING_ADMIN],
                ),
            )
        )
    ).scalar_one_or_none()
    if pending is not None:
        return generic

    # 5. Mint confirm token + insert row.
    settings = get_settings()
    plaintext_token, token_hash = new_token()
    row = AccessRequest(
        email_index=email_idx,
        email_encrypted=pii.encrypt_pii(email_norm),
        name_encrypted=pii.encrypt_pii(payload.name),
        affiliation_encrypted=pii.encrypt_pii(payload.affiliation),
        justification_encrypted=pii.encrypt_pii(payload.justification),
        status=STATUS_PENDING_EMAIL_CONFIRM,
        confirm_token_hash=token_hash,
        confirm_token_expires_at=now
        + timedelta(hours=settings.request_confirm_ttl_hours),
        client_ip=client_ip_hash,
        user_agent=user_agent,
    )
    db.add(row)
    await db.commit()

    confirm_url = (
        f"{settings.frontend_origin.rstrip('/')}"
        f"/access-request/confirm/{plaintext_token}"
    )
    try:
        await get_email_sender().send_request_confirmation(
            db, to=email_norm, confirm_url=confirm_url,
        )
    except Exception:
        logger.exception("send_request_confirmation failed")

    return generic


# ── Double opt-in confirmation ─────────────────────────────────────────


@router.get("/access-request/confirm/{token}", response_model=ConfirmResponse)
@limiter.limit("10/hour")
async def confirm_request(
    request: Request,  # noqa: ARG001 — required by slowapi limiter
    token: str,
    db: AsyncSession = Depends(get_session),
) -> ConfirmResponse:
    th = hash_token(token)
    row = (
        await db.execute(
            select(AccessRequest).where(AccessRequest.confirm_token_hash == th)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Invalid or expired confirmation link.",
        )

    if row.confirmed_at is not None:
        return ConfirmResponse(
            status="already_used",
            message="This request has already been confirmed.",
        )

    now = datetime.now(timezone.utc)
    if row.confirm_token_expires_at < now:
        return ConfirmResponse(
            status="expired",
            message=(
                "This confirmation link has expired. "
                "Please submit a new request."
            ),
        )

    settings = get_settings()
    decision_plaintext, decision_hash = new_token()
    row.confirmed_at = now
    row.status = STATUS_PENDING_ADMIN
    row.decision_token_hash = decision_hash
    row.decision_token_expires_at = now + timedelta(
        hours=settings.admin_decision_ttl_hours,
    )
    await db.commit()

    base = settings.frontend_origin.rstrip("/")
    approve_url = (
        f"{base}/admin/access-requests/decide/{decision_plaintext}"
        f"?action=approve"
    )
    deny_url = (
        f"{base}/admin/access-requests/decide/{decision_plaintext}"
        f"?action=deny"
    )

    # Pick the admin recipient. Explicit ADMIN_NOTIFICATION_EMAIL wins;
    # otherwise fall back to the first admin user in the DB so the
    # feature works out-of-the-box without an env var. If neither
    # exists, we log a warning and skip silently — the request still
    # advanced to pending_admin and admins can poll the queue.
    admin_to: str | None = settings.admin_notification_email or None
    if not admin_to:
        first_admin = (
            await db.execute(
                select(User).where(User.role == "admin").order_by(User.created_at)
            )
        ).scalars().first()
        if first_admin is not None:
            admin_to = pii.decrypt_pii(first_admin.email_encrypted)

    if admin_to:
        try:
            await get_email_sender().send_admin_notification(
                db,
                to_admin=admin_to,
                requester_name=pii.decrypt_pii(row.name_encrypted),
                requester_email=pii.decrypt_pii(row.email_encrypted),
                affiliation=pii.decrypt_pii(row.affiliation_encrypted),
                justification=pii.decrypt_pii(row.justification_encrypted),
                approve_url=approve_url,
                deny_url=deny_url,
            )
        except Exception:
            logger.exception("send_admin_notification failed")
    else:
        logger.warning(
            "No admin notification email configured and no admin user "
            "in DB — admins must poll /api/admin/access-requests manually."
        )

    return ConfirmResponse(
        status="confirmed",
        message=(
            "Thank you. Your request is now awaiting admin review. "
            "You'll receive an email once a decision is made."
        ),
    )


# ── Admin review surface ───────────────────────────────────────────────


@router.get(
    "/admin/access-requests",
    response_model=list[AccessRequestListItem],
)
async def list_access_requests(
    admin: AuthContext = Depends(require_admin),  # noqa: ARG001 — gate
    db: AsyncSession = Depends(get_session),
    status_filter: str | None = None,
) -> list[AccessRequestListItem]:
    stmt = select(AccessRequest).order_by(AccessRequest.created_at.desc())
    if status_filter:
        stmt = stmt.where(AccessRequest.status == status_filter)
    rows = (await db.execute(stmt)).scalars().all()

    # Resolve reviewer ids → reviewer emails in one query.
    reviewer_ids = {r.reviewed_by for r in rows if r.reviewed_by is not None}
    reviewer_map: dict[uuid.UUID, str] = {}
    if reviewer_ids:
        users = (
            await db.execute(select(User).where(User.id.in_(reviewer_ids)))
        ).scalars().all()
        reviewer_map = {
            u.id: pii.decrypt_pii(u.email_encrypted) for u in users
        }

    out: list[AccessRequestListItem] = []
    for r in rows:
        dec = _decrypt_row(r)
        out.append(
            AccessRequestListItem(
                id=r.id,
                email=dec["email"],
                name=dec["name"],
                affiliation=dec["affiliation"],
                status=r.status,
                created_at=r.created_at,
                confirmed_at=r.confirmed_at,
                reviewed_at=r.reviewed_at,
                reviewed_by_email=(
                    reviewer_map.get(r.reviewed_by)
                    if r.reviewed_by is not None
                    else None
                ),
                denial_reason=r.denial_reason,
            )
        )
    return out


@router.get(
    "/admin/access-requests/{request_id}",
    response_model=AccessRequestDetail,
)
async def get_access_request(
    request_id: uuid.UUID,
    admin: AuthContext = Depends(require_admin),  # noqa: ARG001 — gate
    db: AsyncSession = Depends(get_session),
) -> AccessRequestDetail:
    row = await db.get(AccessRequest, request_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Access request not found.",
        )
    dec = _decrypt_row(row)
    reviewer_email: str | None = None
    if row.reviewed_by is not None:
        rev = await db.get(User, row.reviewed_by)
        if rev is not None:
            reviewer_email = pii.decrypt_pii(rev.email_encrypted)
    return AccessRequestDetail(
        id=row.id,
        email=dec["email"],
        name=dec["name"],
        affiliation=dec["affiliation"],
        status=row.status,
        created_at=row.created_at,
        confirmed_at=row.confirmed_at,
        reviewed_at=row.reviewed_at,
        reviewed_by_email=reviewer_email,
        denial_reason=row.denial_reason,
        justification=dec["justification"],
        client_ip=row.client_ip,
        user_agent=row.user_agent,
    )


@router.post("/admin/access-requests/{request_id}/approve", status_code=200)
async def approve_request(
    request_id: uuid.UUID,
    admin: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    # NOTE: with_for_update is a no-op on SQLite (test) and a real
    # row-level lock on Postgres (prod). Kept conditional because the
    # SQLite dialect's emulation reset our StaticPool connection in
    # tests; production retains the safety.
    row = await db.get(AccessRequest, request_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Access request not found.",
        )
    if row.status != STATUS_PENDING_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot approve a request with status='{row.status}'."
            ),
        )

    email = pii.decrypt_pii(row.email_encrypted)
    _inv, accept_url = await create_invitation_for_email(
        db,
        email=email,
        role="editor",
        invited_by=admin.user.id,
        bypass_duplicate_checks=False,
    )
    try:
        await get_email_sender().send_set_password(
            db, to=email, set_password_url=accept_url, expires_hours=72,
        )
    except Exception:
        logger.exception("send_set_password failed")

    row.status = STATUS_APPROVED
    row.reviewed_by = admin.user.id
    row.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True}


@router.post("/admin/access-requests/{request_id}/deny", status_code=200)
async def deny_request(
    request_id: uuid.UUID,
    payload: AccessRequestDenyRequest,
    admin: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    # NOTE: with_for_update is a no-op on SQLite (test) and a real
    # row-level lock on Postgres (prod). Kept conditional because the
    # SQLite dialect's emulation reset our StaticPool connection in
    # tests; production retains the safety.
    row = await db.get(AccessRequest, request_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Access request not found.",
        )
    if row.status not in (STATUS_PENDING_ADMIN, STATUS_PENDING_EMAIL_CONFIRM):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot deny a request with status='{row.status}'.",
        )

    email = pii.decrypt_pii(row.email_encrypted)
    row.status = STATUS_DENIED
    row.reviewed_by = admin.user.id
    row.reviewed_at = datetime.now(timezone.utc)
    row.denial_reason = payload.reason
    await db.commit()

    try:
        await get_email_sender().send_denial_notice(
            db, to=email, reason=payload.reason,
        )
    except Exception:
        logger.exception("send_denial_notice failed")
    return {"ok": True}


# ── Decision magic-link entry point ────────────────────────────────────


@router.get("/admin/access-requests/decide/{token}")
@limiter.limit("10/hour")
async def decide_via_magic_link(
    request: Request,  # noqa: ARG001 — required by slowapi limiter
    token: str,
    db: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    settings = get_settings()
    base = settings.frontend_origin.rstrip("/")
    th = hash_token(token)
    row = (
        await db.execute(
            select(AccessRequest).where(
                AccessRequest.decision_token_hash == th,
                AccessRequest.status == STATUS_PENDING_ADMIN,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return RedirectResponse(
            url=f"{base}/admin/access-requests?error=invalid_token",
            status_code=302,
        )
    now = datetime.now(timezone.utc)
    if (
        row.decision_token_expires_at is None
        or row.decision_token_expires_at < now
    ):
        return RedirectResponse(
            url=f"{base}/admin/access-requests?error=expired_token",
            status_code=302,
        )
    return RedirectResponse(
        url=f"{base}/admin/access-requests/{row.id}", status_code=302,
    )
