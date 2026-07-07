# AI Extraction — NER backends and the Modal app

> Up: [AI Extraction](README.md)

## How it works — 2. Inference backends and EXTRACTION_MODE

`resolve_mode()` (`extraction_backend.py:109`): explicit argument >
`EXTRACTION_MODE` env var > `"local"` default. Production runs `modal`
(`heroku config:set EXTRACTION_MODE=modal MODAL_NER_URL=https://…modal.run`).
All three backends implement one `InferenceBackend` Protocol
(`warm_up`, `person_ner`, `provenance_ner`, `contents_ner`, `genre_classify`),
so `_process_one_record` is backend-agnostic. `warm_up()` returns a
`ModelAvailability` report; an unreachable model degrades to "no predictions
for that role", never a session failure.

The **Modal backend** POSTs `{text, title, notes, models}` to
`MODAL_NER_URL/extract` and memoises the last response so the four per-role
methods share one round trip per record. Every call routes through
`cache_lookup_or_call` on the shared `inference_cache` table (kinds
`ner.person`, `ner.provenance`, `ner.contents`, `genre.classify` — all
content-addressed, no expiry; Redis L1 automatic per Rule W-25). Network
errors return `[]` after logging.

## How it works — 3. The Modal app (`modal/modal_app.py`)

`modal deploy modal_app.py` builds an image that: (1) pip-installs pinned
deps; (2) **pre-bakes all weights** (`_bake_weights` → `/weights`) from six
HF repos — joint-ner-v2, provenance/contents `.pt`, genre classifier `.pt`,
the shared DictaBERT encoder, and TaatikNet; (3) vendors the desktop's
`ner/` + `converter/authority/` + `converter/wikidata/` trees via
`add_local_dir(..., copy=True)`. `_bake_weights` runs **before** the
local-dir adds so editing desktop NER code never invalidates the ~3 GB weight
layer (Rule W-15). `MhmNer` (`cpu=2, memory=4096, scaledown_window=300,
max_containers=4`) loads all four models once per container in
`@modal.enter` and serves `/extract`, `/transliterate`, and `/health` from
one ASGI base URL. The FastAPI backend **never imports** anything from
`modal/` — communication is HTTPS only. Cold start after 5 min idle:
~5–60 s; warm calls ~200–400 ms; ≈$0.00006/record.
