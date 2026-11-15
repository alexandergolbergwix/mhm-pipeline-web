# Caching stack — Rules

> Up: [Caching stack](README.md)

1. **R1 — All new external inference/API call sites MUST go through
   `cache_lookup_or_call` (or `cache_http_call`); NEVER hand-roll a lookup.**
   *Why:* the Redis L1, TTL policy, skip-cache invalidation, and cross-user
   sharing are all automatic behind that one entry point (Rule W-25).
2. **R2 — NEVER cache a decision input that must observe live state**
   (Wikidata reconciliation right before a create/merge, Turnstile checks).
   *Why:* a stale cached answer would silently drive an irreversible write —
   see the docstring of `endpoint_query_summary` (`inference_cache.py:147`).
3. **R3 — A cache failure MUST NEVER break the caller.** Redis GET/SET and
   Postgres reads and writes log failures.
   A failed read releases its transaction before `fetch()`.
   Only `fetch()` errors propagate. *Why:* the cache is an optimisation, not a dependency.
4. **R4 — Regular misses NEVER cache `None` / empty-list results.**
   *Why:* transient model/network errors must not become sticky negatives.
   (Only `skip_cache=True` writes empties — as deliberate invalidation.)
5. **R5 — Every on-disk per-run build result MUST have a Postgres
   write-through counterpart** (Rule W-39). *Why:* Heroku wipes the dyno
   filesystem on every deploy; an on-disk-only cache is state that silently
   evaporates and re-costs a multi-minute rebuild (`HmoCoverageCache` incident).
6. **R6 — NEVER invalidate a fingerprint-keyed build cache manually.**
   *Why:* the fingerprint covers every input; changing the data is the
   invalidation (Rule W-26 cache). If a rebuild isn't triggering, the bug is
   a missing input in the fingerprint, not a missing delete.
7. **R7 — New cache kinds MUST be added to both `KIND_TTL` and
   `_REDIS_TTL_SECONDS`.** *Why:* an absent `KIND_TTL` entry means
   never-expires in Postgres and no-expiry in Redis — usually wrong for
   mutable upstream data, and the prune job will never reclaim it.
8. **R8 — hit_count/last_hit_at UPDATEs are skipped on Redis hits — do not
   "fix" this.** *Why:* that per-warm-hit Postgres write was the main
   warm-run latency cost of the old single-tier design (`inference_cache.py:29-34`).
9. **R9 — Cache keys MUST be content-addressed via `canonical_hash` and free
   of volatile fields.** *Why:* sorted-keys canonical JSON + `_strip_volatile`
   guarantee two semantically-equal calls share one row across users.
10. **R10 — AI verdict caches (`kind=ai_verdict`) MUST fingerprint the full
    judge input and store that fingerprint as `cache_key` on every persist
    path (Rule W-51).** *Why:* eval-agent prompt hashes and bare entity ids
    do not change when labels, MARC, claims, or match payloads change —
    stale pills would survive rebuilds. `override_cache` is for unchanged-input
    re-judge only; input changes must auto-miss via `sanitise_stale_*` on reads.
11. **R11 — Content fingerprints MUST exclude volatile metadata (Rule W-208).**
    Shared fingerprint projections MUST remove nested transport timestamps,
    request IDs, and fetch times. They MUST retain semantic date values. *Why:*
    metadata changed cache keys for unchanged content.
12. **R12 — Local Wikidata data supplies candidates, never global absence or edit permission (W-339).** Preserve full snapshots and source time. Keep foreign targets read-only. *Why:* a partial local index cannot certify a new public item or authorize an edit.
13. **R13 — Batch saved query scopes before the request cap (W-340).** Read at most 500 scopes per query. Retain fresh candidates beyond the cap. *Why:* one read per planned query made a capped production resume slow.
