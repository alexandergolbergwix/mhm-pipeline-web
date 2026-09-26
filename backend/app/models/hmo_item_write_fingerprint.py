"""Per-item write-dedup fingerprints for the HMO Wikibase upload.

One row per uploaded item: a content hash of exactly what the update
write would set (payload labels, descriptions, resolved claim triples).
When the live item's last recorded fingerprint equals the current
payload's, an ``Update published entries`` upload skips the wiki call —
a re-write would be a byte-for-byte no-op. The row is refreshed after
every successful create/update so the next pass skips cleanly.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CHAR, DateTime, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _new_uuid


class HmoItemWriteFingerprint(Base):
    __tablename__ = "hmo_item_write_fingerprints"
    __table_args__ = (
        UniqueConstraint("run_id", "local_id", name="uq_hmo_item_fp_run_local"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    local_id: Mapped[str] = mapped_column(CHAR(256), nullable=False)
    wikibase_id: Mapped[str | None] = mapped_column(CHAR(32), nullable=True)
    payload_fingerprint: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    written_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
