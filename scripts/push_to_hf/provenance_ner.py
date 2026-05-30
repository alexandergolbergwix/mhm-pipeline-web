"""Push the Provenance NER checkpoint to HuggingFace Hub.

Run once per checkpoint update. See scripts/push_to_hf/README.md.

Source: pipeline/ner/provenance_ner_model.pt
Target: alexgoldberg/hebrew-manuscript-provenance-ner-v2

Labels (BIO): OWNER / DATE / COLLECTION on MARC 561.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (   # type: ignore[import-not-found]
    configure_logging, load_checkpoint, parse_args, save_and_push,
)

logger = logging.getLogger("push_to_hf.provenance")


_LABELS = ["O", "B-OWNER", "I-OWNER", "B-DATE", "I-DATE", "B-COLLECTION", "I-COLLECTION"]


def main() -> int:
    configure_logging()
    args = parse_args()

    ckpt = load_checkpoint(args.checkpoint)

    # Try to read label maps from the checkpoint; fall back to the
    # static schema above.
    id2label = ckpt.get("id2label") or {i: l for i, l in enumerate(_LABELS)}
    label2id = ckpt.get("label2id") or {l: i for i, l in enumerate(_LABELS)}
    id2label = {int(k): v for k, v in id2label.items()}

    from transformers import (  # noqa: PLC0415
        AutoModelForTokenClassification, AutoTokenizer,
    )

    logger.info("Building AutoModelForTokenClassification (base=%s, labels=%d)",
                 args.base_model, len(id2label))
    model = AutoModelForTokenClassification.from_pretrained(
        args.base_model,
        num_labels=len(id2label),
        id2label=id2label,
        label2id=label2id,
        ignore_mismatched_sizes=True,
    )

    # Apply head weights from the checkpoint. The desktop saves the
    # full state_dict — try a strict load first; fall back to a non-
    # strict load that just patches the head.
    state = ckpt.get("state_dict") or ckpt
    if isinstance(state, dict):
        try:
            model.load_state_dict(state, strict=True)
            logger.info("Loaded state_dict strictly ✓")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Strict load failed (%s) — retrying non-strict", exc)
            missing, unexpected = model.load_state_dict(state, strict=False)
            logger.info("Non-strict load: missing=%d, unexpected=%d",
                         len(missing), len(unexpected))
    else:
        logger.warning("Checkpoint has no state_dict — pushing untrained head")

    tokenizer = AutoTokenizer.from_pretrained(args.base_model)

    save_and_push(
        model=model, tokenizer=tokenizer, repo=args.repo,
        private=args.private, dry_run=args.dry_run,
        model_card_kwargs={
            "title": "Hebrew manuscript Provenance NER",
            "summary": (
                "BIO token-classifier (OWNER / DATE / COLLECTION) on "
                "MARC 561 provenance fields of Hebrew manuscripts. "
                "Trained on the MHM (Mapping Hebrew Manuscripts) corpus."
            ),
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
