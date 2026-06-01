# /deploy-modal

Deploy the Modal NER app (`modal/modal_app.py`) and tell the user the
URL to set on the backend.

## What to do

```bash
cd /Users/alexandergo/Documents/Doctorat/mhm-pipeline-web/modal
modal deploy modal_app.py
```

Two image builds happen:
1. **First image** (~3 min on cache hit, ~5 min cold): pip install +
   `_bake_weights()` pulls all weight artefacts (~3 GB) from the user's
   HF repos and the DictaBERT base.
2. **Second image** (~10 s): `add_local_dir × 2` copies the desktop's
   `ner/` + `converter/authority/` source + the `touch __init__.py`
   step.

The deploy ends with one URL like:
```
Created Web Function URL for MhmNer.web => https://<workspace>--mhm-ner-mhmner-web.modal.run
```

That URL goes into the backend's `MODAL_NER_URL` env var. The backend
appends `/extract` and `/health` itself.

## Tail container logs (cold-start debugging)

```bash
modal app logs mhm-ner
```

Look for `MhmNer: all four models loaded` — that's the success
sentinel from `@modal.enter()`. If you don't see it within ~60 s of
the first request, the load() step is hung or erroring.

## Common build / runtime failures

1. **`Desktop pipeline repo not found at /pipeline`** — a pre-flight
   check ran inside the build container. Should be impossible after
   commit 98a0ca6; if it recurs, check that `modal_app.py` has no
   top-level `Path.exists()` guards.
2. **`An image tried to run a build step after using add_local_*`** —
   reorder so `run_function(_bake_weights)` is BEFORE `add_local_dir`,
   and pass `copy=True` on every `add_local_dir`. See commit c19b9f3.
3. **`No such file or directory: ../pipeline/ner`** — the Modal app
   resolves `PIPELINE_ROOT` as a sibling of the web repo. If the user
   relocated the desktop pipeline, point at the new path via env or
   edit the constant.
4. **`ModuleNotFoundError: No module named 'sklearn'`** (or any other
   missing module) at `@modal.enter()` time — the desktop's training
   scripts import other libs at module top (e.g. sklearn, pandas).
   `JointNERPipeline` lazily imports `train_joint_entity_role_model_kfold`
   which pulls those in. Add the missing package to
   `modal/requirements.txt`. Surfaced by `modal app logs mhm-ner`.
   Fixed for sklearn in commit 4f5b765.
5. **`ConnectionError` to dictabert at @modal.enter()** — the
   `NERInferencePipeline` doesn't honour `MHM_BUNDLED_DICTABERT` and
   may attempt an HF Hub fetch at load time. The image bakes
   DictaBERT to `/weights/dicta-il__dictabert/` so the network call
   succeeds if egress is allowed; if not, port the env-var awareness
   from `JointNERPipeline._resolve_model_directory_alias` to the
   generic loader.

## Cost reminder

Modal bills per-second of container time + memory. CPU-2 at ~$0.14/hr
warm, $0 idle. The `$30/mo` free credit covers tens of thousands of
records for sporadic curator use. Don't enable a GPU container unless
the user explicitly asks — CPU is plenty for these BERT models.

## Hand-off to the user

After a successful deploy, paste the URL and tell them to set:

```bash
heroku config:set EXTRACTION_MODE=modal MODAL_NER_URL=<URL>
heroku restart -a <app>
```

The next time they open the extraction page, all four model pills
should turn green.
