"""Reuse recent, source-bound authority reads across the audit and repair stages."""

import json
import zlib
from base64 import b64decode, b64encode
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

CACHE_KEY = "_gila_authority_evidence"
_MAX_AGE = timedelta(hours=1)


@dataclass
class AuthorityEvidence:
    fingerprint: str
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    bibs: dict = field(default_factory=dict)
    frames: dict = field(default_factory=dict)
    viaf: dict = field(default_factory=dict)
    reused: int = 0

    @classmethod
    def load(cls, stored, fingerprint):
        if not isinstance(stored, dict) or stored.get("fingerprint") != fingerprint:
            return cls(fingerprint)
        observed_at = datetime.fromisoformat(stored["observed_at"])
        if not timedelta(0) <= datetime.now(UTC) - observed_at < _MAX_AGE:
            return cls(fingerprint)
        data = json.loads(zlib.decompress(b64decode(stored["data"])))
        return cls(
            fingerprint, observed_at, data["bibs"],
            {cn: {key: frozenset(ids) for key, ids in rows.items()} for cn, rows in data["frames"].items()},
            {aid: frozenset(tuple(key) for key in keys) for aid, keys in data["viaf"].items()},
        )

    def dump(self):
        data = {
            "bibs": self.bibs,
            "frames": {cn: {key: sorted(ids) for key, ids in rows.items()} for cn, rows in self.frames.items()},
            "viaf": {aid: sorted(keys) for aid, keys in self.viaf.items()},
        }
        return {
            "fingerprint": self.fingerprint,
            "observed_at": self.observed_at.isoformat(),
            "data": b64encode(zlib.compress(json.dumps(data, separators=(",", ":")).encode())).decode(),
        }

    async def read(self, group, requested, fetch, progress):
        rows = getattr(self, group)
        missing = set(requested) - rows.keys()
        reused = len(requested) - len(missing)
        self.reused += reused
        observed_empty_viaf = {}

        async def combined_progress(processed, total):
            if progress:
                await progress(reused + processed, reused + total)

        if missing:
            fresh = await fetch(missing, combined_progress)
            rows.update({key: result for key, result in fresh.items() if key in missing and result})
            if group == "viaf":
                observed_empty_viaf = {key: result for key, result in fresh.items() if key in missing and not result}
        elif reused:
            await combined_progress(0, 0)
        return {**observed_empty_viaf, **{key: rows[key] for key in requested if key in rows}}
