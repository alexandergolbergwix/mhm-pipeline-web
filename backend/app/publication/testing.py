"""Local adapters for publication behavior tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from datetime import datetime

from app.publication.core import PublicationModule
from app.publication.gateway import FakeWikidataGateway, WikidataGateway
from app.publication.repository import InMemoryPublicationRepository
from app.publication.types import (
    ProfileRef,
    PublicationEntityInput,
    SourceSnapshotRef,
)


class StaticProjectionSource:
    def __init__(
        self,
        entities_by_snapshot: Mapping[str, Sequence[PublicationEntityInput]],
        current_source_digests: Mapping[str, str] | None = None,
    ) -> None:
        self._entities_by_snapshot = entities_by_snapshot
        self._current_source_digests = current_source_digests

    async def project(
        self,
        source_snapshot: SourceSnapshotRef,
        profile: ProfileRef,
    ) -> AsyncIterator[tuple[PublicationEntityInput, ...]]:
        del profile
        entities = sorted(
            self._entities_by_snapshot[source_snapshot.snapshot_id],
            key=lambda entity: entity.entity_key,
        )
        for start in range(0, len(entities), 1_000):
            yield tuple(entities[start : start + 1_000])

    async def is_current(
        self,
        run_id: str,
        source_snapshot: SourceSnapshotRef,
    ) -> bool:
        del run_id
        if self._current_source_digests is None:
            return source_snapshot.snapshot_id in self._entities_by_snapshot
        return (
            self._current_source_digests.get(source_snapshot.snapshot_id) == source_snapshot.digest
        )


def create_in_memory_publication_module(
    *,
    entities_by_snapshot: Mapping[str, Sequence[PublicationEntityInput]],
    gateway: WikidataGateway | None = None,
    clock: Callable[[], datetime] | None = None,
    current_source_digests: Mapping[str, str] | None = None,
) -> PublicationModule:
    return PublicationModule(
        projection_source=StaticProjectionSource(
            entities_by_snapshot,
            current_source_digests=current_source_digests,
        ),
        repository=InMemoryPublicationRepository(),
        gateway=gateway or FakeWikidataGateway(),
        clock=clock,
    )
