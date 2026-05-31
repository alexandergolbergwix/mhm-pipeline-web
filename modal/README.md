# MHM NER on Modal

Bundles the four Stage-2 inference models — Person NER (joint v2),
Provenance NER, Contents NER, Genre classifier — into one Modal app
and exposes them as one HTTPS endpoint. The FastAPI backend calls
this endpoint per record, so the dyno stays small and there's no
HF-Serverless dependency.

## Layout

| File | Purpose |
|---|---|
| `modal_app.py` | Modal app definition: image, weights download, `MhmNer` class, `extract` web endpoint |
| `bundled/` | Vendored desktop inference modules (`ner/inference_pipeline.py`, etc.) — copied at build time |
| `requirements.txt` | Pinned versions for reproducible image builds |

## Setup (one-time)

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

## Pay-per-call economics

Modal bills per-second of container time. For Hebrew manuscript records:
- 1 record × 4 models ≈ 1.5s CPU = $0.00006
- 100 records ≈ $0.006
- 10,000 records ≈ $0.60

Free tier covers $30/month — research workloads pay $0.

## Cold start

First call after 5 min idle: ~5–15s (loading 4 models into RAM).
Subsequent calls inside the warm window: ~200–400 ms each.
