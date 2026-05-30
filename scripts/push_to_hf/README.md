# Push the three local Stage 2 models to HuggingFace Hub

The web app's HF Inference Providers backend (Mode B in
`backend/app/pipeline/extraction_backend_hf.py`) needs the four NER /
classifier checkpoints reachable as HF models. Person NER is already
on the Hub (`alexgoldberg/hebrew-manuscript-joint-ner-v2`). The other
three live as local `.pt` files in the desktop pipeline tree:

| Model | Checkpoint (desktop) | Target HF repo |
|---|---|---|
| Provenance NER | `pipeline/ner/provenance_ner_model.pt` | `alexgoldberg/hebrew-manuscript-provenance-ner-v2` |
| Contents NER   | `pipeline/ner/contents_ner_model.pt`   | `alexgoldberg/hebrew-manuscript-contents-ner` |
| Genre classifier | `pipeline/ner/genre_classifier_model.pt` | `alexgoldberg/hebrew-manuscript-genre-classifier` |

After running each push script, the corresponding model will work in
Mode B. Until then, the HF backend returns `[]` for that role (Mode A
still works for everything since the .pt files stay on your dev box).

## Prereqs (one-time)

1. Be logged in on the Hub:

   ```bash
   pip install huggingface_hub
   huggingface-cli login                    # paste a Write token
   ```

   *Or* set `HF_TOKEN` in your environment — the push helpers honour it.

2. Click "Accept terms" on `dicta-il/dictabert` once on your HF
   account so the tokenizer can be re-bundled into the new repos.

3. Activate the desktop pipeline's venv (the one with `torch` +
   `transformers` installed; the web backend's `.venv` works too):

   ```bash
   source /Users/alexandergo/Documents/Doctorat/pipeline/.venv/bin/activate
   ```

## Run

Each script is idempotent — re-running pushes the same content, no
side effects beyond a new commit on the Hub.

```bash
cd /Users/alexandergo/Documents/Doctorat/mhm-pipeline-web

python scripts/push_to_hf/provenance_ner.py \
  --checkpoint /Users/alexandergo/Documents/Doctorat/pipeline/ner/provenance_ner_model.pt \
  --repo alexgoldberg/hebrew-manuscript-provenance-ner-v2 \
  --private

python scripts/push_to_hf/contents_ner.py \
  --checkpoint /Users/alexandergo/Documents/Doctorat/pipeline/ner/contents_ner_model.pt \
  --repo alexgoldberg/hebrew-manuscript-contents-ner \
  --private

python scripts/push_to_hf/genre_classifier.py \
  --checkpoint /Users/alexandergo/Documents/Doctorat/pipeline/ner/genre_classifier_model.pt \
  --repo alexgoldberg/hebrew-manuscript-genre-classifier \
  --private
```

## Verify each push worked

Open the model's HF page (e.g.
`https://huggingface.co/alexgoldberg/hebrew-manuscript-provenance-ner-v2`)
and confirm the right-side "Inference API" widget loads.

Then back in the web app:

```bash
cd /Users/alexandergo/Documents/Doctorat/mhm-pipeline-web/backend
export EXTRACTION_MODE=hf-api
.venv/bin/python -c "
import asyncio
from app.pipeline.extraction_backend_hf import HfApiInferenceBackend
async def main():
    b = HfApiInferenceBackend(hf_token='<your token>')
    print(await b.warm_up())
    print(await b.provenance_ner('בעלי הכתב יד הראשון היה …'))
asyncio.run(main())
"
```

When all three pushes are done, set `EXTRACTION_MODE=hf-api` (or pick
the radio in Settings) and Stage 2 runs against HF.

## What each script does

1. Loads the `.pt` checkpoint into a `transformers`
   `AutoModelForTokenClassification` (NER) or
   `AutoModelForSequenceClassification` (genre) with the matching
   `id2label` / `label2id` map.
2. Saves the model via `model.save_pretrained(tmpdir)`.
3. Re-bundles the DictaBERT tokenizer (`dicta-il/dictabert`) into the
   same dir so the Inference API doesn't need a separate tokenizer
   reference.
4. `HfApi.create_repo(private=True)` then `upload_folder()`.

If your checkpoint uses a custom head that doesn't load into the
standard `AutoModel*` shape, the script will warn and exit; in that
case open `scripts/push_to_hf/<model>.py` and adapt the loader (the
files are short and well-commented).
