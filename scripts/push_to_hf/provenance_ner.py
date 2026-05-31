"""Push the Provenance NER checkpoint to HuggingFace Hub.

Architecture: DictaBERT encoder + 2-layer custom NER head (Linear →
ReLU → Dropout → Linear). Doesn't fit stock BertForTokenClassification,
so the repo is published with custom-code (``modeling_mhm_ner.py`` +
``auto_map``) — usable as an artifact + by AutoModel.from_pretrained
with ``trust_remote_code=True``. Not deployable to HF's serverless
Inference Providers tier (which only auto-deploys standard
architectures). The MHM Pipeline's web app keeps loading the .pt file
at runtime for this model.

Source: pipeline/ner/provenance_ner_model.pt
Target: alexgoldberg/hebrew-manuscript-provenance-ner-v2
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

logger = logging.getLogger("push_to_hf.provenance")


def main() -> int:
    configure_logging()
    args = parse_args()
    ckpt = load_checkpoint(args.checkpoint)

    # Desktop saves the head + encoder under ``model_state_dict``.
    state = ckpt.get("model_state_dict") or ckpt.get("state_dict") or ckpt
    if not isinstance(state, dict):
        logger.error("Could not find state_dict in checkpoint")
        return 2

    # Label maps — desktop's key is ``ner_label2id``.
    label2id = (
        ckpt.get("ner_label2id")
        or ckpt.get("label2id")
        or {
            "O": 0,
            "B-OWNER": 1, "I-OWNER": 2,
            "B-DATE": 3,  "I-DATE": 4,
            "B-COLLECTION": 5, "I-COLLECTION": 6,
        }
    )
    id2label = {v: k for k, v in label2id.items()}
    num_labels = len(label2id)

    # Build the MhmNerModel against the DictaBERT base + load the
    # state_dict. The encoder keys (``bert.*``) match directly; head
    # keys (``ner_intermediate.*`` + ``ner_output.*``) match too.
    from transformers import (  # noqa: PLC0415
        AutoConfig, AutoTokenizer, BertModel,
    )
    from modeling_mhm_ner import MhmNerConfig, MhmNerModel  # noqa: PLC0415

    base_cfg = AutoConfig.from_pretrained(args.base_model)
    # Pull BertConfig fields off the base config so BertModel
    # construction works the same way as on the desktop. Strip fields
    # we set explicitly below so there's no double-pass.
    _drop = {"num_labels", "id2label", "label2id", "model_type",
             "architectures", "hidden_size", "head_hidden_size"}
    base_fields = {k: v for k, v in base_cfg.to_dict().items() if k not in _drop}
    config = MhmNerConfig(
        **base_fields,
        model_type="mhm_ner",
        num_labels=num_labels,
        hidden_size=base_cfg.hidden_size,
        head_hidden_size=base_cfg.hidden_size // 2,
        id2label=id2label,
        label2id=label2id,
    )
    config.auto_map = {
        "AutoConfig": "modeling_mhm_ner.MhmNerConfig",
        "AutoModel":  "modeling_mhm_ner.MhmNerModel",
        "AutoModelForTokenClassification": "modeling_mhm_ner.MhmNerModel",
    }

    model = MhmNerModel(config)
    missing, unexpected = model.load_state_dict(state, strict=False)
    logger.info("Load: missing=%d, unexpected=%d", len(missing), len(unexpected))
    if missing:
        logger.warning("Missing keys (first 5): %s", missing[:5])
    if unexpected:
        logger.warning("Unexpected keys (first 5): %s", unexpected[:5])
    # Sanity check: ner head weights present.
    sd = model.state_dict()
    head_norm = sd["ner_output.weight"].abs().sum().item()
    assert head_norm > 0, "ner_output.weight is zero — head failed to load!"
    logger.info("ner_output.weight L1 norm = %.3f (head loaded ✓)", head_norm)

    tokenizer = AutoTokenizer.from_pretrained(args.base_model)

    # Save to a temp dir + include the custom modeling file so the
    # repo is self-contained.
    out = Path(tempfile.mkdtemp(prefix="hf_push_"))
    logger.info("Saving to %s", out)
    model.save_pretrained(out)
    tokenizer.save_pretrained(out)
    # Copy the custom modeling.py alongside the weights.
    shutil.copy2(
        Path(__file__).parent / "modeling_mhm_ner.py",
        out / "modeling_mhm_ner.py",
    )
    (out / "README.md").write_text(_README, encoding="utf-8")

    if args.dry_run:
        logger.info("DRY RUN — files saved at %s; not uploading.", out)
        return 0

    from huggingface_hub import HfApi  # noqa: PLC0415
    api = HfApi()
    logger.info("Creating %s (private=%s) …", args.repo, args.private)
    api.create_repo(
        repo_id=args.repo, repo_type="model",
        private=args.private, exist_ok=True,
    )
    api.upload_folder(
        folder_path=str(out), repo_id=args.repo, repo_type="model",
        commit_message="push from push_to_hf/provenance_ner.py",
    )
    logger.info("✓ Pushed: https://huggingface.co/%s", args.repo)
    shutil.rmtree(out, ignore_errors=True)
    return 0


_README = """# Hebrew manuscript Provenance NER

BIO token-classifier (OWNER / DATE / COLLECTION) trained on MARC 561
provenance fields of Hebrew manuscripts. Part of the MHM (Mapping
Hebrew Manuscripts) Pipeline.

## Architecture

DictaBERT encoder + a 2-layer NER head (Linear 768→384 → ReLU →
Dropout → Linear 384→7). The head doesn't fit stock
`BertForTokenClassification`, so this repo ships custom code in
`modeling_mhm_ner.py`. Load with:

```python
from transformers import AutoModel, AutoTokenizer
model = AutoModel.from_pretrained(
    "alexgoldberg/hebrew-manuscript-provenance-ner-v2",
    trust_remote_code=True,
)
tok = AutoTokenizer.from_pretrained(
    "alexgoldberg/hebrew-manuscript-provenance-ner-v2",
)
```

## Labels

`O`, `B-OWNER`, `I-OWNER`, `B-DATE`, `I-DATE`, `B-COLLECTION`,
`I-COLLECTION`.

## Status

- F1 on the desktop training corpus: ~0.96
- **Not deployable to HF Inference Providers serverless tier** (custom
  code). Use locally or on a dedicated HF Inference Endpoint.

## Citation

Trained as part of the MHM Pipeline at Bar-Ilan University. See
https://github.com/alexgoldberg/mhm-pipeline for the full training
pipeline and the desktop integration.
"""


if __name__ == "__main__":
    sys.exit(main())
