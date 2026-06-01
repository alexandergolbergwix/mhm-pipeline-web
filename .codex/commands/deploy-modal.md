# /deploy-modal

Deploy the Modal NER app (`modal/modal_app.py`) and surface the URL
that goes into the FastAPI backend's `MODAL_NER_URL` env var.

## Run

```bash
cd /Users/alexandergo/Documents/Doctorat/mhm-pipeline-web/modal
modal deploy modal_app.py
```

Two image builds happen on a fresh deploy:

1. **First image** — `pip_install_from_requirements` then
   `run_function(_bake_weights)` pulls ~3 GB of model weights from
   the user's HF repos and the DictaBERT base. ~5 min cold, ~30 s
   cached.
2. **Second image** — `add_local_dir × 2` copies the desktop's
   `ner/` + `converter/authority/` source, then a `touch __init__.py`
   step makes the namespaces importable. ~10 s.

The final line of the deploy is the URL to set on Heroku:

```
Created Web Function URL for MhmNer.web => https://<workspace>--mhm-ner-mhmner-web.modal.run
```

Tell the user to set it:

```bash
heroku config:set EXTRACTION_MODE=modal MODAL_NER_URL=<URL>
heroku restart -a <app>
```

The backend appends `/extract` and `/health` to that base URL itself.

## Tail container logs (debugging cold-start hangs)

```bash
modal app logs mhm-ner
```

Success sentinel: `MhmNer: all four models loaded`. If you don't see
it within ~60 s of the first request, the load() step is hanging or
erroring. The traceback (if any) appears earlier in the log.

## Pitfalls — read before editing modal_app.py

| Symptom | Fix | Commit |
|---|---|---|
| `Desktop pipeline repo not found at /pipeline` (build container) | Remove top-level `Path.exists()` guards; `add_local_dir` errors clearly enough on its own | 98a0ca6 |
| `An image tried to run a build step after using add_local_*` | `run_function(_bake_weights)` MUST come before `add_local_dir`; pass `copy=True` on every `add_local_dir` | c19b9f3 |
| Two URLs (`-extract` and `-health`) instead of one | Use `@modal.asgi_app()` mounting a FastAPI router, not two `@modal.fastapi_endpoint` decorators | eda32c8 |
| `ModuleNotFoundError` at `@modal.enter()` | Some desktop module imports a third-party lib at top level (sklearn, pandas, etc.). Add to `modal/requirements.txt` | 4f5b765 (sklearn) |

## Pay-per-call economics

| Resource | Rate | Per call |
|---|---|---|
| CPU-2 container time | $0.000076/sec | ~$0.0001 / record |
| 4 GB memory | $0.0000096/sec | ~$0.00001 / record |

`scaledown_window=300` keeps containers warm 5 min after the last
call. After that, $0. Modal's $30/mo free credit covers tens of
thousands of records for sporadic curator use.

## Do NOT

- Enable a GPU container without the user asking. CPU is plenty for
  these BERT models (~300 ms per record).
- Import `modal_app.py` from the FastAPI backend. The trust boundary
  is HTTPS-only (CLAUDE.md Rule W-15).
- Skip the `modal app logs mhm-ner` step when debugging cold starts.
  The HTTP timeout never tells you why the container hung — only the
  container's own stdout does.
