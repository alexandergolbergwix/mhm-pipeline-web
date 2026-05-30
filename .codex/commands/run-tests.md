# /run-tests

Run the full three-layer test pyramid.

```bash
# Backend (pytest + httpx.AsyncClient)
cd /Users/alexandergo/Documents/Doctorat/mhm-pipeline-web/backend
.venv/bin/python -m pytest tests/ -v

# Frontend unit (Vitest + React Testing Library)
cd /Users/alexandergo/Documents/Doctorat/mhm-pipeline-web/frontend
yarn test:unit

# Browser e2e (Playwright — one-time `npx playwright install chromium`)
yarn test:e2e
```

See `docs/testing.md` for the test catalogue and desktop-parity targets.
