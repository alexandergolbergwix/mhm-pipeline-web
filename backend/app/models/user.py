"""User model.

Every PII column is encrypted at rest. ``email`` is also indexed via an
HMAC blind index so login lookups stay O(1) without ever storing the
plaintext email in a searchable form.
"""

from __future__ import annotations

import uuid

from sqlalchemy import LargeBinary, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, _new_uuid

ROLE_ADMIN = "admin"
ROLE_EDITOR = "editor"


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )

    # Login lookup — HMAC-SHA256(EMAIL_HMAC_KEY, lower(email)).
    # Unique + indexed so login is O(log n).
    email_index: Mapped[bytes] = mapped_column(
        LargeBinary(32), nullable=False, unique=True, index=True,
    )
    # PII at rest — AES-256-GCM(MASTER_KEY, email). Random nonce per write.
    email_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    name_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    # Password hash (Argon2id; verify-only path, separate from KEK derivation).
    password_hash: Mapped[str] = mapped_column(String, nullable=False)

    # Per-user salt for KEK derivation (Argon2id(password, kek_salt) → KEK).
    # Generated once at user creation, never rotated unless the password
    # changes; on password change we keep the salt and re-derive KEK from
    # the new password so wrapped DEKs can be re-wrapped in place.
    kek_salt: Mapped[bytes] = mapped_column(LargeBinary(16), nullable=False)

    # Org-wide role. ``admin`` can invite + manage users; ``editor`` is a
    # regular collaborator. Project-level roles (owner/editor/viewer) come
    # in Phase 3's ``memberships`` table and override this for per-project
    # operations.
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_EDITOR)
