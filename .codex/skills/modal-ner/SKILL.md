# Skill — modal-ner

Operate the Modal-hosted NER + Genre classifier app at
`mhm-pipeline-web/modal/modal_app.py`. This is the production
extraction backend for AI Extraction ("AI Extraction") in the web port.

## When to invoke

Any prompt that asks to:
- Deploy or redeploy the Modal NER app.
- Debug cold-start hangs on `https://*.modal.run` endpoints.
- Add a new model to the Modal container or update an existing one.
- Switch the web backend between extraction modes (local / hf-api /
  modal).
- Investigate `agent_runner` → `ModalInferenceBackend` round-trip
  latency or error rates.

## Read first

Before touching `modal_app.py`:

1. `mhm-pipeline-web/CLAUDE.md` Rules **W-11** (three backends, one
   selector), **W-12** (shared inference cache routes every call),
   **W-15** (modal/ is a deploy target, never an import).
2. `mhm-pipeline-web/modal/README.md` — image layout + economics.
3. `mhm-pipeline-web/.codex/commands/deploy-modal.md` — the deploy
   command + pitfall index.

## The model bundle

One `MhmNer` container loads all four desktop pipelines:

| Model | Class | Weights |
|---|---|---|
| Joint Person NER (role-aware) | `JointNERPipeline` | `alexgoldberg/hebrew-manuscript-joint-ner-v2` (safetensors) |
| Provenance NER | `NERInferencePipeline` | `alexgoldberg/hebrew-manuscript-provenance-ner` (.pt) |
| Contents NER | `NERInferencePipeline` | `alexgoldberg/hebrew-manuscript-contents-ner` (.pt) |
| Genre classifier | `GenreClassifier` | `alexgoldberg/hebrew-manuscript-genre-classifier` (.pt) |

DictaBERT (the base encoder shared by the three derived NER models) is
also baked at `/weights/dicta-il__dictabert` and `JointNERPipeline`
points at it via the `MHM_BUNDLED_DICTABERT` env var.

## Image build order (immutable)

```
1. pip_install_from_requirements
2. run_function(_bake_weights)          ← downloads weights from HF
3. add_local_dir(ner, copy=True)        ← desktop NER source
4. add_local_dir(converter/authority, copy=True)
5. run_commands(touch __init__.py …)
6. env(PYTHONPATH, TOKENIZERS_PARALLELISM, MHM_BUNDLED_DICTABERT)
```

**Never re-order.** Modal rejects any `run_function` / `run_commands`
that comes after an `add_local_*` unless `copy=True` is set on the
local-add. Putting `_bake_weights` before the local adds also keeps
the weight layer stable when the desktop NER source changes.

## Common runtime failures

| Symptom (in `modal app logs mhm-ner`) | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'sklearn'` | Some desktop module imports `sklearn` at top level. Add to `modal/requirements.txt`. Same pattern for pandas, scipy, etc. |
| `Connection refused` to `huggingface.co` during `@modal.enter()` | `NERInferencePipeline` is trying to download DictaBERT instead of reading `/weights/dicta-il__dictabert/`. Patch the generic loader to honour `MHM_BUNDLED_DICTABERT`. |
| `JointNERPipeline` constructor stalls > 60 s | The config.json route through `_is_role_aware_hf_model` may be reading a missing file. Check `/weights/alexgoldberg__hebrew-manuscript-joint-ner-v2/config.json` exists. |
| 502 or hang from `/health` | Almost always `@modal.enter()` crashed — read `modal app logs mhm-ner`. The HTTP timeout never tells you why. |

## Backend integration

The FastAPI backend calls Modal via
`backend/app/pipeline/extraction_backend_modal.py::ModalInferenceBackend`.
Selection lives in `extraction_backend.py::build_backend()` (Rule
W-11). Caching is via `inference_cache` (Rule W-12) — every `extract`
call goes through `cache_lookup_or_call`.

Trust boundary (Rule W-15): the backend NEVER imports anything from
`modal/`. Communication is HTTPS only. `MODAL_NER_URL` is the single
configuration point.

## Do NOT

- Run `modal deploy` from the desktop pipeline repo. The Modal app
  lives in the web repo; deploys must run from there.
- Import `modal_app.py` into the backend's import graph. The Modal
  CLI runtime is a deploy/build tool, not a backend dependency.
- Add models that aren't in the four-model bundle without first
  updating CLAUDE.md Rule W-11.
- Switch the container to GPU without justification. CPU-2 handles
  these BERT models in ~300 ms / call; GPU would be 5× cost for no
  user-visible latency improvement.
