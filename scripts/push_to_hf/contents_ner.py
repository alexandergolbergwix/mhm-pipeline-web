"""Push the Contents NER checkpoint to HuggingFace Hub.

Same architecture as the Provenance NER (DictaBERT + 2-layer custom
head) — published as a custom-code repo. Not deployable to HF
Inference Providers serverless tier. See provenance_ner.py for the
full rationale.

Source: pipeline/ner/contents_ner_model.pt
Target: alexgoldberg/hebrew-manuscript-contents-ner
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

logger = logging.getLogger("push_to_hf.contents")


def main() -> int:
    configure_logging()
    args = parse_args()
    ckpt = load_checkpoint(args.checkpoint)

    state = ckpt.get("model_state_dict") or ckpt.get("state_dict") or ckpt
    if not isinstance(state, dict):
        logger.error("Could not find state_dict in checkpoint")
        return 2

    label2id = (
        ckpt.get("ner_label2id")
        or ckpt.get("label2id")
        or {
            "O": 0,
            "B-WORK":         1, "I-WORK":         2,
            "B-FOLIO":        3, "I-FOLIO":        4,
            "B-WORK_AUTHOR":  5, "I-WORK_AUTHOR":  6,
        }
    )
    id2label = {v: k for k, v in label2id.items()}
    num_labels = len(label2id)

    from transformers import AutoConfig, AutoTokenizer  # noqa: PLC0415
    from modeling_mhm_ner import MhmNerConfig, MhmNerModel  # noqa: PLC0415

    base_cfg = AutoConfig.from_pretrained(args.base_model)
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
    sd = model.state_dict()
    head_norm = sd["ner_output.weight"].abs().sum().item()
    assert head_norm > 0, "ner_output.weight is zero — head failed to load!"
    logger.info("ner_output.weight L1 norm = %.3f (head loaded ✓)", head_norm)

    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    out = Path(tempfile.mkdtemp(prefix="hf_push_"))
    logger.info("Saving to %s", out)
    model.save_pretrained(out)
    tokenizer.save_pretrained(out)
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
    api.create_repo(
        repo_id=args.repo, repo_type="model",
        private=args.private, exist_ok=True,
    )
    api.upload_folder(
        folder_path=str(out), repo_id=args.repo, repo_type="model",
        commit_message="push from push_to_hf/contents_ner.py",
    )
    logger.info("✓ Pushed: https://huggingface.co/%s", args.repo)
    shutil.rmtree(out, ignore_errors=True)
    return 0


_README = """# Hebrew manuscript Contents NER

BIO token-classifier (WORK / FOLIO / WORK_AUTHOR) trained on MARC 505
contents fields of Hebrew manuscripts. Part of the MHM (Mapping
Hebrew Manuscripts) Pipeline.

Architecture: DictaBERT encoder + 2-layer custom NER head (Linear →
ReLU → Dropout → Linear). Custom code in `modeling_mhm_ner.py`. Load
with `trust_remote_code=True`. **Not on HF Inference Providers
serverless tier**; use locally or on a dedicated endpoint.
"""


if __name__ == "__main__":
    sys.exit(main())
