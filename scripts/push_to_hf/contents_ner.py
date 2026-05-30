"""Push the Contents NER checkpoint to HuggingFace Hub.

Run once per checkpoint update. See scripts/push_to_hf/README.md.

Source: pipeline/ner/contents_ner_model.pt
Target: alexgoldberg/hebrew-manuscript-contents-ner

Labels (BIO): WORK / FOLIO / WORK_AUTHOR on MARC 505.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (   # type: ignore[import-not-found]
    configure_logging, load_checkpoint, parse_args, save_and_push,
)

logger = logging.getLogger("push_to_hf.contents")


_LABELS = [
    "O",
    "B-WORK",        "I-WORK",
    "B-FOLIO",       "I-FOLIO",
    "B-WORK_AUTHOR", "I-WORK_AUTHOR",
]


def main() -> int:
    configure_logging()
    args = parse_args()

    ckpt = load_checkpoint(args.checkpoint)
    id2label = ckpt.get("id2label") or {i: l for i, l in enumerate(_LABELS)}
    label2id = ckpt.get("label2id") or {l: i for i, l in enumerate(_LABELS)}
    id2label = {int(k): v for k, v in id2label.items()}

    from transformers import (  # noqa: PLC0415
        AutoModelForTokenClassification, AutoTokenizer,
    )
    model = AutoModelForTokenClassification.from_pretrained(
        args.base_model,
        num_labels=len(id2label),
        id2label=id2label,
        label2id=label2id,
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
            "title": "Hebrew manuscript Contents NER",
            "summary": (
                "BIO token-classifier (WORK / FOLIO / WORK_AUTHOR) on "
                "MARC 505 contents fields of Hebrew manuscripts."
            ),
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
