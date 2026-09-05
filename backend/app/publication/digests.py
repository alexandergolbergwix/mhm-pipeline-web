"""Canonical JSON and digest functions for immutable publication data."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import cast

from app.publication.types import JsonValue, PublicationEntityInput


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_digest(value: object) -> str:
    payload = canonical_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(slots=True)
class ReleaseDigestAccumulator:
    """Build a release digest without retaining the entity corpus."""

    metadata: object
    _hash: object = field(init=False, repr=False)

    def __post_init__(self) -> None:
        digest = hashlib.sha256()
        digest.update(b"wikidata-publication-release-v1\0")
        self._update_part(digest, canonical_json(self.metadata).encode("utf-8"))
        self._hash = digest

    def add_entity(self, entity_key: str, digest: str) -> None:
        hasher = cast("hashlib._Hash", self._hash)
        self._update_part(hasher, entity_key.encode("utf-8"))
        self._update_part(hasher, digest.encode("ascii"))

    def hexdigest(self) -> str:
        return cast("hashlib._Hash", self._hash).hexdigest()

    @staticmethod
    def _update_part(hasher: object, value: bytes) -> None:
        typed_hasher = cast("hashlib._Hash", hasher)
        typed_hasher.update(len(value).to_bytes(8, "big"))
        typed_hasher.update(value)


@dataclass(slots=True)
class CanonicalSequenceDigest:
    """Digest an ordered record stream with bounded memory."""

    schema: str
    metadata: object
    _hash: object = field(init=False, repr=False)

    def __post_init__(self) -> None:
        digest = hashlib.sha256()
        digest.update(self.schema.encode("utf-8"))
        digest.update(b"\0")
        ReleaseDigestAccumulator._update_part(
            digest,
            canonical_json(self.metadata).encode("utf-8"),
        )
        self._hash = digest

    def add(self, value: object) -> None:
        ReleaseDigestAccumulator._update_part(
            self._hash,
            canonical_json(value).encode("utf-8"),
        )

    def hexdigest(self) -> str:
        return cast("hashlib._Hash", self._hash).hexdigest()


def entity_digest(entity: PublicationEntityInput) -> str:
    return canonical_digest(
        {
            "schema": "wikidata-publication-entity-v1",
            "entity_key": entity.entity_key,
            "entity_type": entity.entity_type,
            "document": entity.document,
            "evidence_refs": sorted(entity.evidence_refs),
            "identity_assertions": sorted(entity.identity_assertions),
            "local_references": sorted(entity.local_references),
        }
    )


def freeze_json(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        frozen = {key: freeze_json(item) for key, item in value.items()}
        return cast(JsonValue, MappingProxyType(frozen))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return cast(JsonValue, tuple(freeze_json(item) for item in value))
    return value


def thaw_json(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [thaw_json(item) for item in value]
    return value
