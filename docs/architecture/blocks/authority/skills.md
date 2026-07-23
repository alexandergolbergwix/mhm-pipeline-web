# Authority Enrichment — Skills

> Up: [Authority Enrichment](README.md)

Standalone Authority UI and mutation playbooks below are historical only. Use HMO Studio creation and the canonical E2E audit for live work.

### Skill: add a new guard
1. Write the pure predicate in `converter/authority/stage3_guards.py` if it is source-agnostic (sync to desktop!), or directly in `authority_hardening.py` if web-only.
2. Add a `guard_*` function returning `GuardVerdict(fired, new_confidence, reason, flag)`.
3. Register it in the `verdicts` list inside `apply_hardening_guards` (`authority_hardening.py:823`); add its flag to the right strip set (`hard_reject_flags`, `_viaf_strip_flags`, `_wikidata_strip_flags`) if it must clear ids.
4. Decide whether the flag blocks auto-approve → extend `AUTO_APPROVE_BLOCKED_GUARDS` in `runs.py:371`.
5. Add the flag description to `guardExplain` in both `AuthorityTable.tsx` and `AuthorityDetailDrawer.tsx`.
6. Extend `tests/unit/test_authority_hardening.py` (and `test_viaf_mazal_guards.py` if VIAF-related).

### Skill: re-import Mazal / KIMA into Heroku Postgres
Idempotent (TRUNCATE + re-import); run locally against the Heroku DSN:
```bash
cd backend && DATABASE_URL=... KIMA_DB_PATH=backend/data/kima/kima_index.db \
  .venv/bin/python -m scripts.import_kima_to_postgres   # ~15 s
cd backend && DATABASE_URL=... MAZAL_DB_PATH=.../mazal_index.db \
  .venv/bin/python -m scripts.import_mazal_to_postgres  # ~10 min
```
The Mazal script auto-detects whether the source SQLite carries
`main_marc_tag` (old files import cleanly with NULL). Ensure migration
`0020_mazal_heading_metadata` ran first. One corrupt ~900 KB KIMA name-index
blob is skipped by design.

### Skill: refresh enrichment through canonical HMO creation
1. Open HMO Wikibase Studio and use **Rebuild** with `refresh_authority=true`; this is the only supported live enrichment entrypoint.
2. The HMO builder runs Mazal, KIMA, VIAF, and Wikidata matching, applies fail-closed guards, and persists accepted evidence on the HMO item.
3. Upload with **Update existing**, read every item back, and run `check_hmo_canonical_gate.py` before enabling canonical projections.
4. The former Authority mutation routes and `authority_re_enrich` job return HTTP 410 by default. Read-only match data remains for provenance and migration audits; set `LEGACY_AUTHORITY_MUTATIONS_ENABLED=true` only for an emergency rollback.

### Skill: resolve a homonym as curator
1. Filter the AuthorityTable Guards column to `homonym_unresolved` (or spot the ⚠ chip).
2. Open the row's `AuthorityDetailDrawer` — the "Multiple Mazal personalities" card lists candidates with dates, heading tag, and score.
3. Click **Pick** on the correct אישיות. The backend sets `mazal_id`, clears abstain flags, stamps `personality_picked_by_curator`, and sets confidence to `medium`.
4. Optionally run AI verify (`.../ai-verify`) and then approve.

### Skill: add a new authority source
1. Add the matcher under `backend/converter/authority/` (or reuse a desktop one via the sync script).
2. Add backend Protocol methods to `AuthorityBackend` and implement in all three backends (Postgres, Local, Modal-stub).
3. In `DesktopMatcher`, add a `_cached`-wrapped call with a new `kind="authority.<name>"`; register Postgres + Redis TTLs in `inference_cache.py` (`KIND_TTL`, `_REDIS_TTL_SECONDS`) — never bypass the cache layer (Rule W-25).
4. Wire it into `_match_one` at the correct kind-routing branch; append to `sources` and `reasoning_parts`; extend the payload contract table in CLAUDE.md Rule W-29.
5. Extend `sources_after` re-derivation (`authority.py:1591`) so the label survives guard stripping honestly.
6. Add routing tests to `tests/unit/test_authority_routing.py`.
