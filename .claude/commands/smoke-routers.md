# /smoke-routers

Smoke-test every FastAPI router import + route registration. Run this
after any backend change to confirm nothing's broken before restarting
uvicorn.

```bash
cd /Users/alexandergo/Documents/Doctorat/mhm-pipeline-web/backend
.venv/bin/python -c "
from app.main import app
for r in app.routes:
    methods = sorted(getattr(r, 'methods', set()))
    path = getattr(r, 'path', '?')
    if path.startswith('/api') and methods:
        print(','.join(methods).ljust(15), path)
"
```

Expected: all five section routers (extraction, rdf, hmo_studio,
wikidata_studio, ai_verify) registered alongside auth / runs / etc.
