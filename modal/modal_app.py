"""Modal app — Stage 2 NER + Genre inference for the MHM Pipeline.

Bundles the four desktop models into one Modal container so the
FastAPI backend can call a single HTTPS endpoint per record:

  * Person NER (joint v2)   — HF: alexgoldberg/hebrew-manuscript-joint-ner-v2
  * Provenance NER          — HF: alexgoldberg/hebrew-manuscript-provenance-ner
  * Contents NER            — HF: alexgoldberg/hebrew-manuscript-contents-ner
  * Genre classifier        — HF: alexgoldberg/hebrew-manuscript-genre-classifier

The image build:
  1. Installs pinned torch + transformers + huggingface_hub.
  2. Vendors the desktop's ``ner/`` + ``converter/authority/`` modules
     (sibling-repo at ../../pipeline/) — these own the inference logic.
  3. Pre-downloads every weight artefact from HF Hub into the image
     at /weights, so cold start is just torch.load(), not network IO.

The ``MhmNer`` class loads all four models once per container
(@modal.enter) and exposes ``/extract`` as a FastAPI POST endpoint
returning all four model outputs for one record.

Deploy:

    cd modal/
    modal deploy modal_app.py

Modal prints the public URL. Set it on the backend:

    heroku config:set EXTRACTION_MODE=modal MODAL_NER_URL=https://...modal.run

Pay-per-call economics: ~$0.00006 per record (4 models, ~1.5s CPU).
Modal's $30/mo free credit covers tens of thousands of records.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import modal

# ── HF weight artefacts to bake into the image ───────────────────────────


# Each entry: (HF repo id, list of files to download).
# joint-ner-v2 is loaded by transformers.AutoTokenizer + a custom
# JointModel class that reads the safetensors weights via the desktop
# pipeline's loader. The three .pt repos use plain torch.load.
WEIGHTS_TO_BAKE: list[tuple[str, list[str]]] = [
    ("alexgoldberg/hebrew-manuscript-joint-ner-v2",
        ["config.json", "tokenizer.json", "tokenizer_config.json",
         "special_tokens_map.json", "vocab.txt", "model.safetensors"]),
    ("alexgoldberg/hebrew-manuscript-provenance-ner",
        ["provenance_ner_model.pt"]),
    ("alexgoldberg/hebrew-manuscript-contents-ner",
        ["contents_ner_model.pt"]),
    ("alexgoldberg/hebrew-manuscript-genre-classifier",
        ["genre_classifier_model.pt"]),
    # DictaBERT base — the three derived NER models share this encoder.
    # Pre-baking avoids a 400 MB download on every cold start.
    ("dicta-il/dictabert",
        ["config.json", "tokenizer.json", "tokenizer_config.json",
         "special_tokens_map.json", "vocab.txt", "model.safetensors"]),
]


def _bake_weights() -> None:
    """Run inside the image-build container to pre-fetch every artefact."""
    from huggingface_hub import hf_hub_download  # noqa: PLC0415

    weights_dir = Path("/weights")
    weights_dir.mkdir(parents=True, exist_ok=True)
    for repo_id, files in WEIGHTS_TO_BAKE:
        # Each repo lands in /weights/<repo_id_slugified>/.
        slug = repo_id.replace("/", "__")
        local_dir = weights_dir / slug
        local_dir.mkdir(parents=True, exist_ok=True)
        for filename in files:
            try:
                hf_hub_download(
                    repo_id=repo_id,
                    filename=filename,
                    local_dir=str(local_dir),
                )
                print(f"baked {repo_id}/{filename}")
            except Exception as exc:  # noqa: BLE001
                # Some optional artefacts (vocab.txt for safetensors-only
                # repos) may not exist; that's fine.
                print(f"skipped {repo_id}/{filename}: {exc}")


# ── Image: code + deps + pre-baked weights ───────────────────────────────


PIPELINE_ROOT = Path(__file__).resolve().parent.parent.parent / "pipeline"

# Sanity check at deploy time so a missing sibling repo errors loudly.
if not (PIPELINE_ROOT / "ner" / "inference_pipeline.py").exists():
    raise SystemExit(
        f"Desktop pipeline repo not found at {PIPELINE_ROOT}. "
        "Expected sibling layout: ../pipeline/ner/inference_pipeline.py"
    )


image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements(
        str(Path(__file__).parent / "requirements.txt"),
    )
    # Desktop NER + classifier source. The whole ner/ tree is needed
    # because the inference pipelines reference each other and the
    # training-model classes (JointModel, NERModel, GenreClassificationModel)
    # are imported at load time to reconstruct the architecture before
    # state_dict.load(). The ignore list keeps the image lean — .pt
    # files come from /weights, not the local copy.
    .add_local_dir(
        local_path=str(PIPELINE_ROOT / "ner"),
        remote_path="/root/ner",
        ignore=["*.pt", "*.pyc", "__pycache__", ".pytest_cache",
                "*.csv", "*.tsv", "*.bin", "datasets/*"],
    )
    .add_local_dir(
        local_path=str(PIPELINE_ROOT / "converter" / "authority"),
        remote_path="/root/converter/authority",
        ignore=["*.pyc", "__pycache__", "*.db"],
    )
    # Empty __init__.py so `converter.authority.genre_classifier` resolves.
    .run_commands(
        "touch /root/converter/__init__.py /root/converter/authority/__init__.py",
        "touch /root/ner/__init__.py",
    )
    # Bake every weight artefact into the image at build time.
    .run_function(_bake_weights)
    .env({
        "PYTHONPATH":          "/root:/root/ner",
        "TOKENIZERS_PARALLELISM": "false",
        # Tell the GenreClassifier to use the bundled DictaBERT path
        # rather than re-downloading at runtime.
        "MHM_BUNDLED_DICTABERT": "/weights/dicta-il__dictabert",
    })
)


# ── The Modal app ────────────────────────────────────────────────────────


app = modal.App("mhm-ner", image=image)


@app.cls(
    cpu=2.0,
    memory=4096,
    # Container stays warm 5 min after the last call. Curators doing
    # back-to-back extractions pay one cold start total.
    scaledown_window=300,
    # Cap concurrency so we don't OOM. Each record only takes ~1.5s.
    max_containers=4,
)
class MhmNer:
    @modal.enter()
    def load(self) -> None:
        """Load all four models once per container."""
        # Imports here so the module-scope (which Modal serialises into
        # the image build) stays small and import errors don't break
        # deploys — they only break the container at warm-up time.
        import sys  # noqa: PLC0415
        sys.path.insert(0, "/root")
        sys.path.insert(0, "/root/ner")

        from inference_pipeline import JointNERPipeline  # noqa: PLC0415
        from ner_inference_pipeline import NERInferencePipeline  # noqa: PLC0415
        from converter.authority.genre_classifier import GenreClassifier  # noqa: PLC0415

        WEIGHTS = "/weights"
        # JointNERPipeline accepts an HF Hub model dir; we point it at
        # the pre-baked copy on disk.
        self.person = JointNERPipeline(
            model_path=f"{WEIGHTS}/alexgoldberg__hebrew-manuscript-joint-ner-v2",
            device="cpu",
        )
        self.provenance = NERInferencePipeline(
            model_path=(
                f"{WEIGHTS}/alexgoldberg__hebrew-manuscript-provenance-ner/"
                "provenance_ner_model.pt"
            ),
            device="cpu",
        )
        self.contents = NERInferencePipeline(
            model_path=(
                f"{WEIGHTS}/alexgoldberg__hebrew-manuscript-contents-ner/"
                "contents_ner_model.pt"
            ),
            device="cpu",
        )
        self.genre = GenreClassifier(
            model_path=(
                f"{WEIGHTS}/alexgoldberg__hebrew-manuscript-genre-classifier/"
                "genre_classifier_model.pt"
            ),
            device="cpu",
        )
        print("MhmNer: all four models loaded")

    @modal.fastapi_endpoint(method="POST", docs=True)
    def extract(self, payload: dict[str, Any]) -> dict[str, Any]:
        """One record → all four model outputs.

        Request body:
            {
              "text":     "...",          # concatenated MARC text fed to NER
              "title":    "...",          # MARC 245 (for genre)
              "notes":    ["...", ...],   # MARC 500 list (for genre)
              "models":   ["person", "provenance", "contents", "genre"]  // optional subset
            }

        Response:
            {
              "person":     [{text, role, confidence, start, end}, ...],
              "provenance": [{text, type, start, end, confidence}, ...],
              "contents":   [{text, type, start, end, confidence}, ...],
              "genre":      [[label, confidence], ...]
            }
        """
        text   = str(payload.get("text") or "")
        title  = str(payload.get("title") or "")
        notes  = list(payload.get("notes") or [])
        models = set(payload.get("models") or
                     ["person", "provenance", "contents", "genre"])

        out: dict[str, Any] = {}
        if "person" in models and text.strip():
            out["person"] = self.person.process_text(text)
        else:
            out["person"] = []
        if "provenance" in models and text.strip():
            out["provenance"] = self.provenance.process_text(text)
        else:
            out["provenance"] = []
        if "contents" in models and text.strip():
            out["contents"] = self.contents.process_text(text)
        else:
            out["contents"] = []
        if "genre" in models:
            preds = self.genre.predict(title=title, notes=notes)
            out["genre"] = [[label, float(conf)] for label, conf in preds]
        else:
            out["genre"] = []
        return out

    @modal.fastapi_endpoint(method="GET")
    def health(self) -> dict[str, Any]:
        """Liveness probe — returns availability per model."""
        return {
            "ok":         True,
            "person":     self.person     is not None,
            "provenance": self.provenance is not None,
            "contents":   self.contents   is not None,
            "genre":      self.genre      is not None,
        }


# ── Local entry point so `modal run modal_app.py` works for smoke tests ──


@app.local_entrypoint()
def smoke() -> None:
    """Smoke-test against a real container. Prints the extracted entities."""
    test_text = (
        "ביאור על התורה / משה בן מימון. "
        "הספר הוא העתקה של כתב יד מהמאה הט\"ז."
    )
    ner = MhmNer()
    result = ner.extract.remote({
        "text":  test_text,
        "title": "ביאור על התורה",
        "notes": [test_text],
    })
    print("person:    ", len(result.get("person", [])))
    print("provenance:", len(result.get("provenance", [])))
    print("contents:  ", len(result.get("contents", [])))
    print("genre:     ", result.get("genre"))
