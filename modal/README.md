# MHM Modal Apps

Two Modal apps serve the MHM Pipeline: one for AI extraction (NER + genre)
and one for authority enrichment (Mazal person lookup + KIMA place lookup).

---

## NER + Genre (`modal_app.py`)

Bundles the four AI Extraction inference models — Person NER (joint v2),
Provenance NER, Contents NER, Genre classifier — into one Modal app
and exposes them as one HTTPS endpoint. The FastAPI backend calls
this endpoint per record, so the dyno stays small and there's no
HF-Serverless dependency.

### Layout

| File | Purpose |
|---|---|
| `modal_app.py` | Modal app definition: image, weights download, `MhmNer` class, `extract` web endpoint |
| `requirements.txt` | Pinned versions for reproducible image builds |

### Setup (one-time)

```bash
pip install modal
modal token new          # opens browser; sign up with Google/GitHub
modal deploy modal_app.py
```

The deploy prints a URL like:
```
https://<workspace>--mhm-ner-extract.modal.run
```

Set that on the FastAPI backend:
```bash
heroku config:set EXTRACTION_MODE=modal MODAL_NER_URL=https://...modal.run
```

### Pay-per-call economics

Modal bills per-second of container time. For Hebrew manuscript records:
- 1 record × 4 models ≈ 1.5s CPU = $0.00006
- 100 records ≈ $0.006
- 10,000 records ≈ $0.60

Free tier covers $30/month — research workloads pay $0.

### Cold start

First call after 5 min idle: ~5–15s (loading 4 models into RAM).
Subsequent calls inside the warm window: ~200–400 ms each.

---

## Authority enrichment (`modal_authority.py`)

Bakes both authority SQLite databases into a CPU-only container and
exposes `POST /match_person` (Mazal, 983 MB index) and `POST /match_place`
(KIMA, 15 MB index) as a FastAPI ASGI app.

On Heroku the slug limit makes it impossible to ship `mazal_index.db`
(983 MB) directly, and the filesystem is ephemeral so any DB copied at
boot is lost on dyno restart. Modal solves both: the DBs are baked into
the image at deploy time and available on every cold start in < 1 s.

The inference cache (`Rule W-12`) wraps every call in `authority.py`
so cache hits never reach Modal — only real misses pay the HTTP round-trip.

### Setup

```bash
modal deploy modal_authority.py
```

The deploy prints a URL like:
```
https://<workspace>--mhm-authority-mhmauthority-web.modal.run
```

Set that on the FastAPI backend:
```bash
heroku config:set AUTHORITY_MODE=modal \
  MODAL_AUTHORITY_URL=https://<workspace>--mhm-authority-mhmauthority-web.modal.run
```

### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `POST` | `/match_person` | Mazal person lookup (`{"name": "..."}`) |
| `POST` | `/match_place` | KIMA place lookup (`{"text": "..."}`) |

### Pay-per-call economics

CPU-only container, ~1 ms per SQLite lookup.
~$0.000002 per call — effectively free at any research volume.

Container stays warm for 5 min after last call (`scaledown_window=300`).

### Redeploy after a DB update

The Mazal and KIMA databases are baked into the image layer. Any update
to the source `.db` file requires a `modal deploy` to take effect.

```bash
# After updating mazal_index.db or kima_index.db:
modal deploy modal_authority.py
```
