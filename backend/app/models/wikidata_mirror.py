"""Public Wikidata snapshots and local indexes; never write authorization."""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WikidataMirrorEntity(Base):
    __tablename__ = "wikidata_mirror_entities"

    qid: Mapped[str] = mapped_column(String(24), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    document: Mapped[dict] = mapped_column(JSONB().with_variant(JSON(), "sqlite"))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)


class WikidataMirrorFact(Base):
    __tablename__ = "wikidata_mirror_facts"
    __table_args__ = (Index("ix_wd_mirror_fact_lookup", "property_id", "value_hash", "qid"),)

    qid: Mapped[str] = mapped_column(
        String(24),
        ForeignKey("wikidata_mirror_entities.qid", ondelete="CASCADE"),
        primary_key=True,
    )
    property_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    value_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class WikidataMirrorScope(Base):
    __tablename__ = "wikidata_mirror_scopes"

    scope_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stored_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress: Mapped[dict] = mapped_column(JSONB().with_variant(JSON(), "sqlite"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
