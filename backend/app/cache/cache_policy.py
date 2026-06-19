"""Per-scope TTL registry for Tier 2 read-model cache."""

from __future__ import annotations

# Redis hot-window seconds — None = no expiry.
SCOPED_REDIS_TTL_SECONDS: dict[str, int | None] = {
    "extraction.entities": 60,
    "dashboard.projects":    60,
    "wikidata.studio":       3600,
}

# In-process memory backstop — used when Redis is absent (CI).
SCOPED_MEMORY_TTL_SECONDS: dict[str, int | None] = {
    "extraction.entities": 300,
    "dashboard.projects":    300,
    "wikidata.studio":     3600,
}
