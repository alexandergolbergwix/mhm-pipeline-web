"""Push the Genre multi-label classifier to HuggingFace Hub.

Run once per checkpoint update. See scripts/push_to_hf/README.md.

Source: pipeline/ner/genre_classifier_model.pt
Target: alexgoldberg/hebrew-manuscript-genre-classifier

The classifier is MULTI-LABEL (sigmoid head). Saved with
problem_type='multi_label_classification' so HF's text-classification
inference task returns scores for every label rather than only the
top-1.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (   # type: ignore[import-not-found]
    configure_logging, load_checkpoint, parse_args, save_and_push,
)

logger = logging.getLogger("push_to_hf.genre")


def main() -> int:
    configure_logging()
    args = parse_args()

    ckpt = load_checkpoint(args.checkpoint)

    # The checkpoint MUST carry label maps — without them we have no
    # way to decode the classifier's output. The desktop training code
    # saves these under either 'labels' (list) or 'id2label' (dict).
    labels = ckpt.get("labels")
    id2label = ckpt.get("id2label")
    label2id = ckpt.get("label2id")
    if id2label is None:
        if not labels:
            logger.error(
                "Checkpoint has no id2label / labels key. "
                "Open this script and adapt the loader to your format.",
            )
            return 2
        id2label = {i: l for i, l in enumerate(labels)}
    id2label = {int(k): v for k, v in id2label.items()}
    if label2id is None:
        label2id = {v: k for k, v in id2label.items()}

    from transformers import (  # noqa: PLC0415
        AutoModelForSequenceClassification, AutoTokenizer,
    )

    logger.info("Building AutoModelForSequenceClassification (base=%s, labels=%d, multi-label)",
                 args.base_model, len(id2label))
    model = AutoModelForSequenceClassification.from_pretrained(
        args.base_model,
        num_labels=len(id2label),
        id2label=id2label,
        label2id=label2id,
        problem_type="multi_label_classification",
        ignore_mismatched_sizes=True,
    )

    state = ckpt.get("state_dict") or ckpt
    if isinstance(state, dict):
        try:
            model.load_state_dict(state, strict=True)
            logger.info("Loaded state_dict strictly ✓")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Strict load failed (%s) — retrying non-strict", exc)
            model.load_state_dict(state, strict=False)

    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    save_and_push(
        model=model, tokenizer=tokenizer, repo=args.repo,
        private=args.private, dry_run=args.dry_run,
        model_card_kwargs={
            "title": "Hebrew manuscript Genre classifier",
            "summary": (
                "Multi-label sigmoid classifier predicting genre labels "
                "for Hebrew manuscripts. Used as a P136 fallback in the "
                "MHM Pipeline's Wikidata Studio."
            ),
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
