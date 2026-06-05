# Skill — mhm-pipeline-web-runtime

Operate the MHM Pipeline Web app: start services, run tests,
smoke-check routers, sync from the desktop pipeline, and follow the
architectural invariants in `CLAUDE.md`.

## When to invoke

Any prompt that asks to:
- Run, stop, or restart the FastAPI backend or Vite frontend.
- Run unit / integration / e2e tests.
- Verify router registration after a backend change.
- Re-mirror `backend/converter/` from the desktop tree.
- Add a new section / route to the 5-section project hierarchy.

## Invariants this skill enforces

Read `CLAUDE.md` Rules W-1 through W-29 in full before suggesting any
architectural change. Highlights:

- **W-1**: eval-agent stays a subprocess (argv + env + stdout). No
  cross-imports.
- **W-2**: AI verify UI never accepts free-text prompts; only
  registry actions in `backend/app/pipeline/agent_actions.py`.
- **W-3**: No "Stage N" in user-facing strings. Use real names:
  AI Extraction · Authority · RDF Graph · HMO Wikibase ·
  Wikidata Studio.
- **W-4 / W-5**: Wikidata + Wikibase Cloud safety guards are
  byte-identical to desktop. Never weaken in isolation.
- **W-6**: Secrets are encrypted per user; passed via env to
  subprocesses, never argv.
- **W-7**: No top-level `import torch / transformers / huggingface_hub`.
- **W-8**: Sync libs go through `asyncio.to_thread` /
  `run_in_threadpool`.
- **W-9**: SSE cancellation SIGTERMs the child subprocess.
- **W-10**: `backend/converter/` is a byte-identical mirror of
  `pipeline/converter/`.
- **W-12**: All external inference calls (NER/authority/LLM) go
  through `cache_lookup_or_call` — never call a vendor SDK directly.
- **W-25**: Redis L1 sits in front of Postgres L2 for inference
  cache. `get_redis()` returns `None` when `REDIS_URL` absent (safe
  fallback). New inference call sites must use `cache_lookup_or_call`.
- **W-26**: Wikidata Studio build result cached in
  `wikidata_studio_cache` table; keyed by `input_fingerprint`. Cache
  auto-invalidates when input data changes — never clear manually.

## Standard commands

See `.codex/commands/*.md` for the executable form. The skill should
prefer calling those over inlining new bash blocks.

## Common pitfalls

- Forgetting to restart uvicorn after a route change → 404 in the
  browser. `/smoke-routers` then `/run-backend`.
- Editing a file in `backend/converter/` without also updating the
  desktop tree → next `/sync-from-desktop` clobbers the edit.
- Adding an `import torch` at module top-level → breaks GUI-only
  smokes. Move it inside the function body.
- Surfacing a free-text prompt textarea in the AI verify modal →
  violates W-2. Add the action to the registry instead.
- Calling a vendor SDK (Modal, VIAF, Wikidata SPARQL) directly in a
  route handler → bypasses W-25 two-tier cache. Always go through
  `cache_lookup_or_call`.
- On Heroku, `REDIS_URL` uses a self-signed `rediss://` cert — the
  client initialises with `ssl_cert_reqs=None` intentionally.
  Never remove that flag or Redis connections will fail.
- **W-28**: Mazal and KIMA live in Heroku Postgres tables
  `mazal_authorities`, `mazal_name_index`, `kima_places`,
  `kima_name_index`. Activated by `AUTHORITY_MODE=postgres`. Import
  scripts in `backend/scripts/import_{mazal,kima}_to_postgres.py` are
  idempotent. Do NOT set `AUTHORITY_MODE=modal` on production — the
  Modal authority app is legacy and no longer deployed.
- **W-29**: `AuthorityMatch.payload` MUST carry `viaf_uri`,
  `wikidata_uri`, `preferred_name_heb` (Mazal > Wikidata he_label),
  `cluster_ids`, `mazal_aleph_id`, `wikidata_he_label`,
  `wikidata_en_description`. All populated by `_match_one` + the new
  `_wikidata_enrich_qid` helper. Never strip these fields downstream.
