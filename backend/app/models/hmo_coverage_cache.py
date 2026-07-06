"""Per-run HMO -> Wikidata projection-coverage build cache.

Durable (Postgres) counterpart to the on-disk coverage JSON written by
``hmo_pipeline.cache_coverage_report``. The on-disk file lives under the
dyno's local filesystem, which every Heroku deploy or dyno restart wipes
(same trust boundary as ``RdfArtifact`` for the RDF TTL). Before this
cache existed, a dyno recycle silently discarded a perfectly valid
coverage report and forced the next viewer to wait through a full
rebuild — 9-14 minutes on a large manuscript corpus — even though the
underlying RDF graph had not changed at all.

One row per run, upserted after every successful build and checked
before enqueueing a rebuild job:

  fingerprint matches  ->  return cached report immediately (no job)
  fingerprint differs  ->  the RDF graph changed since the last build; rebuild
  no cache row         ->  rebuild
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CHAR, DateTime, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _new_uuid


class HmoCoverageCache(Base):
    __tablename__ = "hmo_coverage_cache"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_hmo_coverage_cache_run"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    # SHA-256 over the RDF TTL bytes the report was built from.
    input_fingerprint: Mapped[str] = mapped_column(CHAR(64), nullable=False)

    report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    built_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
