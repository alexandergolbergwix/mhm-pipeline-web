# Testing

The web app uses the same three-layer pyramid the desktop pipeline did,
adapted for the stack:

| Layer | Tool | Lives in | What it exercises |
|---|---|---|---|
| **Unit / component** (frontend) | Vitest + React Testing Library | `frontend/tests/unit/` | One component or one pure function at a time. Fast, deterministic, jsdom. |
| **Route / integration** (backend) | pytest + `httpx.AsyncClient` over `ASGITransport` | `backend/tests/integration/` | Real FastAPI app + in-memory SQLite. No network. |
| **Unit** (backend) | pytest | `backend/tests/unit/` | Pure functions — registries, post-filters, parsers. No DB. |
| **Safety guards** (backend) | pytest | `backend/tests/safety/` | Ports of the desktop's 495 Wikidata safety-guard tests. Currently scaffolded; ports land file-by-file. |
| **End-to-end** | Playwright (chromium) | `frontend/e2e/` | True browser hitting a running backend + Vite dev server. |

## Commands

```bash
# Frontend — unit + component
cd frontend
yarn test:unit            # one-shot CI run
yarn test:unit:watch      # HMR for active development
yarn test:unit -- --ui    # the @vitest/ui browser dashboard

# Frontend — e2e
yarn test:e2e             # headless chromium
yarn test:e2e:ui          # UI mode for stepping through
npx playwright install chromium    # one-time browser install

# Backend
cd backend
.venv/bin/python -m pytest tests/ -v                 # full suite
.venv/bin/python -m pytest tests/unit/ -v            # unit only
.venv/bin/python -m pytest tests/integration/ -v     # route tests
.venv/bin/python -m pytest tests/safety/ -v          # ported guards
```

## Running a single test

```bash
# Frontend
cd frontend
yarn test:unit tests/unit/SelectAllVisible.test.tsx
yarn test:unit -t "indeterminate"           # by test name substring

# Backend
cd backend
.venv/bin/python -m pytest tests/unit/test_agent_actions.py -v
.venv/bin/python -m pytest tests/unit/test_agent_actions.py::TestRegistryShape -v
.venv/bin/python -m pytest tests/ -k "render_goal"

# Playwright
cd frontend
./node_modules/.bin/playwright test smoke.spec.ts
./node_modules/.bin/playwright test --grep "login form"
```

## Backend test environment

The backend test stack runs against **SQLite (in-memory) instead of
Postgres** so the suite is hermetic. Three patches make the
production code path work unchanged:

1. **JSONB → JSON** and **UUID(as_uuid=True) → CHAR(36)** compile rules
   are registered on the SQLite dialect at conftest load time.
2. **`create_async_engine`** is wrapped so `pool_size` /
   `max_overflow` are stripped for sqlite URLs (those args are
   Postgres-pool kwargs that SQLite + StaticPool reject).
3. **SQLite-naive datetimes** loaded from `DateTime(timezone=True)`
   columns are coerced to UTC-aware. Production (Postgres
   `TIMESTAMPTZ`) returns aware datetimes; SQLite does not.

The realtime Postgres `LISTEN` bridge is patched to a no-op for the
test session.

### Shared fixtures (in `backend/tests/conftest.py`)

| Fixture | Scope | What it gives you |
|---|---|---|
| `async_client` | function | `httpx.AsyncClient` wired to the FastAPI app via `ASGITransport`. Cookies persist across calls on the same client. |
| `db_session` | function | One `AsyncSession`. Every table truncated at teardown. |
| `auth_user` | function | `(User, authed_client)` — user created + session minted + cookie set on `async_client`. |
| `sample_run` | function | Dict with `project_id`, `run_id`, `match_id`, `control_number`, `client`. One project + run + one AuthorityMatch ready to query. |

### Backend tests must not contact the network

The desktop pipeline blocks all HTTP via an autouse fixture; we have
not enforced that yet on the web backend, but the same rule applies:
**no real Gemini, HuggingFace, VIAF, or Wikidata calls**. For
AI-verify flows, use the `use_no_llm=true` stub-judge path; for
authority lookups, mock the matcher.

## Frontend test environment

Vitest runs under jsdom with these shims:

- **`@testing-library/jest-dom/vitest`** matchers
  (`toBeInTheDocument`, `toHaveAttribute`, …)
- **`ResizeObserver`** mock (jsdom doesn't ship one)
- **`matchMedia`** stub
- **Automatic `cleanup()`** after each test so renders don't leak

Path alias `@` → `frontend/src/` matches the production Vite config so
imports work the same in tests as at runtime.

## End-to-end

Playwright e2e tests assume **the backend (`uvicorn`) and Vite dev
server are already running** on `localhost:8000` + `localhost:5173`
respectively. Override the frontend URL with `E2E_BASE_URL=...`. The
spec dir is `frontend/e2e/`; shared fixtures live under
`frontend/e2e/fixtures/`.

Browsers must be installed once with `npx playwright install chromium`.

## Desktop-parity targets

The desktop pipeline (the PyQt6 app this web app replaces) carries
**1,159 tests** across the same three layers. Test-by-test parity is
**explicitly not the goal** — most desktop tests pin PyQt-specific
behaviour (Qt signal/slot wiring, `QThread` lifecycle, GUI tab
navigation) that has no analogue on the web. The targets below pin
the layers where parity DOES matter:

| Desktop layer | Desktop count | Web target | Status |
|---|---:|---:|---|
| Safety guards (`test_safety_guards.py`) | 495 | 495 | Scaffolded — `backend/tests/safety/` ready for the port. The Wikidata uploader + reconciler that the guards protect is byte-identical between web and desktop (see `docs/project-hierarchy-plan.md` reuse map). |
| GUI / widget tests (pytest-qt) | 254 | Component tests (Vitest + RTL) | In progress — `AgentFlowDiagram` + `SelectAllVisible` covered. **Authority editor components were retired after canonical HMO migration telemetry**; the extraction editor and channel-specific AI verification modals remain covered. Authority mutation routes are tested as fail-closed compatibility paths. |
| Unit (pure-Python — matchers, reducers, post-filters) | ~280 | Same count, ported | In progress. The agent-actions registry is covered; ports of `ner_post_filters.py`, `stage3_guards.py`, `hebrew_translit.py`, `property_mapping.py` follow. |
| Integration (route + worker handshakes) | 130 | ~80 (route tests) | In progress — `ai_verify` endpoints covered; `runs`, `projects`, `auth`, `wikidata_studio` follow. |
| End-to-end | 0 desktop / N e2e | ~25 | Smoke covered. Add one e2e per curator-facing user flow. |

The desktop's Rules 23, 25, 26, 28, 38, 42 (the "never bypass" safety
contracts in `pipeline/CLAUDE.md`) translate directly to the web
backend because the Wikidata code lives in the same `converter/`
package, copied byte-identical. Rule-38's four-stage modification
guard is the highest-priority port target — it caught the
2026-04-12 mass-edit incident and must remain regression-tested on
both stacks.

## Wikidata property-constraint regression suite (added 2026-06-04)

`backend/tests/unit/test_item_validator.py` — **18 tests** pinning the
four property-constraint violations discovered in the 2026-06-04 full
audit. Each test class maps to a specific community-reported failure
mode:

| Class | Checks | Source incident |
|---|---|---|
| `TestP50OnManuscript` | P50 must never appear directly on a manuscript item | Property:P50 constraint page |
| `TestP7416AsQuantity` | P7416 is a string citation qualifier — never use as `value_type=quantity` | 2026-06-04 property audit |
| `TestP31WrongQid` | P31 blocklist (Q179808 = Palme d'Or, Q5 on manuscripts) | Q_PALIMPSEST copy-paste error |
| `TestBadValueQid` | Any statement/qualifier value in `_KNOWN_BAD_VALUE_QIDS` | Q21857942 = Stolpersteine in Upper Austria used as Q_POSSIBLY |
| `TestBuilderNeverViolatesNewChecks` | Integration: `build_items_for_run` output passes all new validator codes | All four bugs caught end-to-end |

The `.codex/commands/audit-wikidata-constants.md` slash command
documents the step-by-step checklist to run before any change to
`property_mapping.py`.

## Next steps (deferred)

1. Port `tests/unit/test_safety_guards.py` from
   `pipeline/tests/unit/` into `backend/tests/safety/`. The
   uploader module imports are byte-identical so the tests should
   mostly carbon-copy across; the only change is the path the test
   reaches into to find `converter.wikidata.uploader`.
2. Wire a `webServer` block in `playwright.config.ts` so CI can boot
   both servers automatically.
3. Add an `allow_http` marker to the backend conftest mirroring the
   desktop's `_block_http` autouse fixture.
4. Add `ruff` + `mypy` to the CI gate alongside pytest.
