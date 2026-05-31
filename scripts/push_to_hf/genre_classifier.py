"""Push the Genre multi-label classifier to HuggingFace Hub.

Architecture: DictaBERT encoder + single Linear classifier head. This
DOES fit stock ``AutoModelForSequenceClassification`` (problem_type
``multi_label_classification``), so the repo is published as a
STANDARD model — usable straight from HF's Inference Providers
serverless tier.

Source: pipeline/ner/genre_classifier_model.pt
Target: alexgoldberg/hebrew-manuscript-genre-classifier
"""

from __future__ import annotations

import logging
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (   # type: ignore[import-not-found]
    configure_logging, load_checkpoint, parse_args,
)

logger = logging.getLogger("push_to_hf.genre")


def main() -> int:
    configure_logging()
    args = parse_args()
    ckpt = load_checkpoint(args.checkpoint)

    state = ckpt.get("model_state_dict") or ckpt.get("state_dict") or ckpt
    if not isinstance(state, dict):
        logger.error("Could not find state_dict in checkpoint")
        return 2

    # Desktop key for the label-id map is ``genre_label2id``.
    label2id = (
        ckpt.get("genre_label2id")
        or ckpt.get("label2id")
        or {}
    )
    if not label2id and ckpt.get("id2label"):
        label2id = {v: int(k) for k, v in ckpt["id2label"].items()}
    if not label2id:
        logger.error("Checkpoint has no genre_label2id / label2id map.")
        return 2
    id2label = {int(v): k for k, v in label2id.items()}
    label2id_normalised = {k: int(v) for k, v in label2id.items()}
    num_labels = len(label2id_normalised)

    base_model = ckpt.get("base_model") or args.base_model

    from transformers import (  # noqa: PLC0415
        AutoModelForSequenceClassification, AutoTokenizer,
    )
    logger.info("Building AutoModelForSequenceClassification "
                 "(base=%s, labels=%d, multi-label)", base_model, num_labels)
    model = AutoModelForSequenceClassification.from_pretrained(
        base_model,
        num_labels=num_labels,
        id2label=id2label,
        label2id=label2id_normalised,
        problem_type="multi_label_classification",
        ignore_mismatched_sizes=True,
    )

    # Strict load — the desktop's head is a single Linear (classifier.*)
    # matching HF's expected key names. If strict fails we fall back
    # but log a loud warning.
    try:
        model.load_state_dict(state, strict=True)
        logger.info("Loaded state_dict strictly ✓")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Strict load failed (%s) — retrying non-strict", exc)
        missing, unexpected = model.load_state_dict(state, strict=False)
        logger.warning("Non-strict: missing=%d, unexpected=%d",
                        len(missing), len(unexpected))
        if missing:
            logger.warning("  missing: %s", missing[:6])
        if unexpected:
            logger.warning("  unexpected: %s", unexpected[:6])

    sd = model.state_dict()
    head_norm = sd["classifier.weight"].abs().sum().item()
    assert head_norm > 0, "classifier.weight is zero — head failed to load!"
    logger.info("classifier.weight L1 norm = %.3f (head loaded ✓)", head_norm)

    tokenizer = AutoTokenizer.from_pretrained(base_model)
    out = Path(tempfile.mkdtemp(prefix="hf_push_"))
    logger.info("Saving to %s", out)
    model.save_pretrained(out)
    tokenizer.save_pretrained(out)
    (out / "README.md").write_text(_render_card(ckpt, id2label), encoding="utf-8")

    if args.dry_run:
        logger.info("DRY RUN — files saved at %s; not uploading.", out)
        return 0

    from huggingface_hub import HfApi  # noqa: PLC0415
    api = HfApi()
    api.create_repo(
        repo_id=args.repo, repo_type="model",
        private=args.private, exist_ok=True,
    )
    api.upload_folder(
        folder_path=str(out), repo_id=args.repo, repo_type="model",
        commit_message="push from push_to_hf/genre_classifier.py",
    )
    logger.info("✓ Pushed: https://huggingface.co/%s", args.repo)
    shutil.rmtree(out, ignore_errors=True)
    return 0


def _render_card(ckpt: dict, id2label: dict) -> str:
    labels = ", ".join(f"`{v}`" for v in id2label.values())
    threshold = ckpt.get("threshold", 0.65)
    f1 = ckpt.get("best_fold_f1") or ckpt.get("mean_fold_f1") or 0.0
    return f"""# Hebrew manuscript Genre classifier

Multi-label sigmoid classifier predicting genre labels for Hebrew
manuscripts. Used as a P136 fallback in the MHM (Mapping Hebrew
Manuscripts) Pipeline's Wikidata Studio.

## Labels ({len(id2label)})

{labels}

## Recommended threshold

`{threshold}` (sigmoid score; pre-sigmoid logits also work with
`logits > 0`).

## Performance

Best fold F1 on the desktop training corpus: `{f1:.3f}`.

## Architecture

DictaBERT encoder + single Linear classifier head with
`problem_type="multi_label_classification"`. This is a STANDARD
architecture — usable directly via HF's Inference Providers
serverless tier:

```python
from huggingface_hub import InferenceClient
client = InferenceClient(token="hf_xxx")
print(client.text_classification(
    text="...",
    model="alexgoldberg/hebrew-manuscript-genre-classifier",
))
```

## Citation

Trained as part of the MHM Pipeline at Bar-Ilan University. See
https://github.com/alexgoldberg/mhm-pipeline for the full training
pipeline.
"""


if __name__ == "__main__":
    sys.exit(main())
