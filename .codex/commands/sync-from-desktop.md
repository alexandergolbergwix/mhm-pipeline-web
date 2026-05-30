# /sync-from-desktop

Re-mirror `backend/converter/` from the desktop pipeline so the
byte-identical-mirror invariant (Rule W-10) stays true.

```bash
DESKTOP=/Users/alexandergo/Documents/Doctorat/pipeline
WEB=/Users/alexandergo/Documents/Doctorat/mhm-pipeline-web

rsync -av --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'mazal_index.db' \
  --exclude 'kima_index.db' \
  "$DESKTOP/converter/" \
  "$WEB/backend/converter/"

# Confirm no drift
diff -q -r --exclude '__pycache__' --exclude '*.pyc' \
  --exclude 'mazal_index.db' --exclude 'kima_index.db' \
  "$DESKTOP/converter" "$WEB/backend/converter" || true
```

After mirroring: re-run `/smoke-routers` + `/run-tests` to catch any
API drift.
