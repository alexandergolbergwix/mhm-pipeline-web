"""Local (in-process) implementation of :class:`InferenceBackend`.

Runs every Stage-2 model inside the FastAPI worker via torch +
transformers. Same behaviour as the original inline-inference code —
this module exists so :mod:`app.pipeline.extraction` doesn't have to
care which backend is configured.

Every model load + every inference call is wrapped in
``asyncio.to_thread`` so the event loop stays free.

Rule W-7: NO top-level ``torch`` / ``transformers`` imports. Every
heavy import is inside a function body so a Mode-B-only deploy can
omit those libs without breaking the import graph.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from app.pipeline.extraction_backend import (
    Entity, GenrePred, InferenceBackend, ModelAvailability,
)

logger = logging.getLogger(__name__)


# Defaults — mirror the constants the historical extraction.py used.
_PERSON_NER_REPO     = "alexgoldberg/hebrew-manuscript-joint-ner-v2"
_PROVENANCE_MODEL    = "provenance_ner_model.pt"
_CONTENTS_MODEL      = "contents_ner_model.pt"
_GENRE_MODEL         = "genre_classifier_model.pt"


class LocalInferenceBackend(InferenceBackend):
    """In-process torch + transformers backend (Mode A)."""

    name = "local"

    def __init__(
        self, *,
        hf_token: str | None,
        overrides: dict[str, str] | None = None,
    ) -> None:
        self._hf_token   = hf_token
        self._overrides  = overrides or {}
        self._person:     Any | None = None
        self._provenance: Any | None = None
        self._contents:   Any | None = None
        self._genre:      Any | None = None
        self._availability: ModelAvailability | None = None

    async def warm_up(self) -> ModelAvailability:
        if self._availability is not None:
            return self._availability
        notes: dict[str, str] = {}
        # Person NER (HF Hub — required).
        person_repo = self._overrides.get("person") or _PERSON_NER_REPO
        try:
            self._person = await asyncio.to_thread(
                _load_person_pipeline, person_repo, self._hf_token,
            )
            notes["person"] = f"loaded from {person_repo}"
        except Exception as exc:  # noqa: BLE001
            logger.exception("LocalInferenceBackend: Person NER load failed")
            notes["person"] = f"load failed: {exc}"
        # Provenance + Contents + Genre — local .pt files. Each returns
        # ``None`` when not on disk; the pipeline tolerates absence.
        self._provenance = await asyncio.to_thread(
            _maybe_load_local_ner, self._overrides.get("provenance") or _PROVENANCE_MODEL,
        )
        notes["provenance"] = "loaded" if self._provenance is not None else "weights absent"
        self._contents = await asyncio.to_thread(
            _maybe_load_local_ner, self._overrides.get("contents") or _CONTENTS_MODEL,
        )
        notes["contents"] = "loaded" if self._contents is not None else "weights absent"
        self._genre = await asyncio.to_thread(
            _maybe_load_genre_classifier, self._overrides.get("genre") or _GENRE_MODEL,
        )
        notes["genre"] = "loaded" if self._genre is not None else "weights absent"

        self._availability = ModelAvailability(
            person=     self._person is not None,
            provenance= self._provenance is not None,
            contents=   self._contents is not None,
            genre=      self._genre is not None,
            notes=      notes,
        )
        return self._availability

    async def person_ner(self, text: str) -> list[Entity]:
        if self._person is None:
            return []
        return await asyncio.to_thread(self._person.process_text, text)

    async def provenance_ner(self, text: str) -> list[Entity]:
        if self._provenance is None:
            return []
        return await asyncio.to_thread(self._provenance.process_text, text)

    async def contents_ner(self, text: str) -> list[Entity]:
        if self._contents is None:
            return []
        return await asyncio.to_thread(self._contents.process_text, text)

    async def genre_classify(self, title: str, notes: list[str]) -> list[GenrePred]:
        if self._genre is None:
            return []
        raw = await asyncio.to_thread(self._genre.predict, title, notes)
        # Normalise the genre classifier's ad-hoc return shapes to a
        # list of dicts. Mirrors what the old _process_one_record did
        # inline.
        out: list[GenrePred] = []
        for item in raw or []:
            if isinstance(item, tuple) and len(item) >= 2:
                out.append({"label": str(item[0]), "confidence": float(item[1])})
            elif isinstance(item, dict):
                out.append({
                    "label":      str(item.get("label") or ""),
                    "confidence": float(item.get("confidence") or 0.0),
                })
        return out


# ── Loaders (lifted verbatim from extraction.py) ──────────────────────


def _load_person_pipeline(repo_id: str, hf_token: str | None) -> Any:
    """Load the joint Person NER model from a HuggingFace repo."""
    import torch  # noqa: PLC0415
    from transformers import AutoTokenizer  # noqa: PLC0415

    from app.pipeline.extraction import _PersonNer  # noqa: PLC0415

    device = "cpu"
    if torch.backends.mps.is_available():
        device = "mps"
    elif torch.cuda.is_available():
        device = "cuda"
    tokenizer = AutoTokenizer.from_pretrained(repo_id, token=hf_token)
    return _PersonNer(repo_id=repo_id, tokenizer=tokenizer, device=device, hf_token=hf_token)


def _maybe_load_local_ner(filename_or_path: str) -> Any | None:
    """Load a Provenance / Contents NER from a local .pt file.

    Returns ``None`` (with a single warning) when the weights aren't
    installed — the pipeline runs without that role's predictions
    rather than failing the whole session.
    """
    weights = _resolve_local_weights(filename_or_path)
    if weights is None:
        logger.info("Local NER weights not found for %s; skipping", filename_or_path)
        return None
    try:
        from ner.ner_inference_pipeline import NERInferencePipeline  # noqa: PLC0415
        return NERInferencePipeline.from_checkpoint(str(weights))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Local NER load failed for %s: %s", weights, exc)
        return None


def _maybe_load_genre_classifier(filename_or_path: str) -> Any | None:
    """Load the multi-label genre classifier from a local .pt."""
    weights = _resolve_local_weights(filename_or_path)
    if weights is None:
        return None
    try:
        from converter.authority.genre_classifier import GenreClassifier  # noqa: PLC0415
        return GenreClassifier.from_checkpoint(str(weights))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Genre classifier load failed: %s", exc)
        return None


def _resolve_local_weights(filename_or_path: str) -> Path | None:
    """Find a local model checkpoint by name OR path. Same lookup order
    the historical extraction.py used."""
    candidate = Path(filename_or_path)
    if candidate.is_file():
        return candidate
    # Try common bundle locations.
    here = Path(__file__).resolve().parents[2]  # backend/
    for guess in (
        here / "state" / "models" / filename_or_path,
        here / "models" / filename_or_path,
        # Dev: weights still live in the desktop pipeline dir.
        here.parent / "pipeline" / "ner" / filename_or_path,
        Path("/Users/alexandergo/Documents/Doctorat/pipeline/ner") / filename_or_path,
    ):
        if guess.is_file():
            return guess
    env = os.environ.get("MHM_MODEL_DIR")
    if env:
        guess = Path(env) / filename_or_path
        if guess.is_file():
            return guess
    return None


__all__ = ["LocalInferenceBackend"]
