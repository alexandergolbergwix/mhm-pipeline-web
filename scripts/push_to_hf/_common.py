"""Shared helpers for the push_to_hf scripts.

Lifted into a common module so each per-model script stays short +
focused. Imports torch + transformers + huggingface_hub eagerly
because every script invokes them — there's no offline mode here.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("push_to_hf")


@dataclass
class PushArgs:
    """Common CLI args for every per-model push script."""

    checkpoint: Path
    repo:       str
    private:    bool
    base_model: str
    dry_run:    bool


def parse_args(*, default_base_model: str = "dicta-il/dictabert") -> PushArgs:
    p = argparse.ArgumentParser(
        description="Push a local Stage-2 .pt checkpoint to HuggingFace Hub.",
    )
    p.add_argument("--checkpoint", type=Path, required=True,
                    help="Path to the .pt file to convert + push.")
    p.add_argument("--repo", required=True,
                    help="Target HF repo id, e.g. alexgoldberg/foo.")
    p.add_argument("--private", action="store_true",
                    help="Create the repo as private (recommended).")
    p.add_argument("--base-model", default=default_base_model,
                    help="HF id of the base tokenizer / encoder.")
    p.add_argument("--dry-run", action="store_true",
                    help="Convert + save locally without uploading.")
    ns = p.parse_args()
    if not ns.checkpoint.is_file():
        p.error(f"checkpoint not found: {ns.checkpoint}")
    return PushArgs(
        checkpoint=ns.checkpoint,
        repo=ns.repo,
        private=bool(ns.private),
        base_model=ns.base_model,
        dry_run=bool(ns.dry_run),
    )


def load_checkpoint(path: Path) -> dict[str, Any]:
    """Load a .pt checkpoint as a dict.

    The desktop checkpoints save ``{"state_dict": …, "config": …,
    "id2label": {…}, "label2id": {…}, …}`` — adapt the loader below if
    your format differs.
    """
    import torch  # noqa: PLC0415
    obj = torch.load(str(path), map_location="cpu", weights_only=False)
    if isinstance(obj, dict):
        return obj
    raise ValueError(f"{path} is not a dict-shaped checkpoint")


def save_and_push(
    *, model: Any, tokenizer: Any, repo: str, private: bool,
    dry_run: bool, model_card_kwargs: dict[str, str] | None = None,
) -> Path:
    """Save model + tokenizer to a temp dir, upload to the Hub.

    Returns the temp dir path so the caller can clean up or inspect.
    """
    import shutil  # noqa: PLC0415
    out = Path(tempfile.mkdtemp(prefix="hf_push_"))
    logger.info("Saving model + tokenizer to %s", out)
    model.save_pretrained(out)
    tokenizer.save_pretrained(out)

    # Drop a minimal README so the HF page isn't empty.
    card_kwargs = model_card_kwargs or {}
    (out / "README.md").write_text(_render_card(card_kwargs), encoding="utf-8")

    if dry_run:
        logger.info("DRY RUN — not uploading. Inspect %s manually.", out)
        return out

    from huggingface_hub import HfApi  # noqa: PLC0415
    api = HfApi()
    logger.info("Creating repo %s (private=%s) …", repo, private)
    api.create_repo(
        repo_id=repo, repo_type="model", private=private, exist_ok=True,
    )
    logger.info("Uploading folder %s → %s …", out, repo)
    api.upload_folder(
        folder_path=str(out),
        repo_id=repo,
        repo_type="model",
        commit_message=f"push from {Path(__file__).parent.name} script",
    )
    logger.info("✓ Push complete: https://huggingface.co/%s", repo)
    shutil.rmtree(out, ignore_errors=True)
    return out


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        stream=sys.stderr,
    )


def _render_card(kwargs: dict[str, str]) -> str:
    title = kwargs.get("title", "MHM Pipeline · Stage 2 model")
    summary = kwargs.get("summary", "")
    return (
        f"# {title}\n\n"
        f"{summary}\n\n"
        "## Usage\n\n"
        "```python\n"
        "from huggingface_hub import InferenceClient\n"
        "client = InferenceClient(token='hf_xxx')\n"
        "print(client.token_classification(text='...', model='REPO_ID'))\n"
        "```\n\n"
        "## Origin\n\n"
        "Trained by the MHM (Mapping Hebrew Manuscripts) project. "
        "See https://github.com/alexgoldberg/mhm-pipeline for the "
        "full training pipeline.\n"
    )
