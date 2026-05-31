"""Inference-backend abstraction for Stage 2 (AI Extraction).

Two implementations share one Protocol:

* :class:`LocalInferenceBackend` — wraps the in-process torch +
  transformers pipelines. The default; ships byte-identical output to
  the historical inline-inference code. Works offline.
* :class:`HfApiInferenceBackend` — calls HuggingFace's Inference
  Providers (formerly Inference API) over HTTPS. Backend container can
  skip torch entirely.

The four model roles are typed identically so ``_process_one_record``
in :mod:`app.pipeline.extraction` doesn't need to know which backend
fired the call.

Choice is made at session start by reading the ``EXTRACTION_MODE``
environment variable (or the per-user setting once that's wired in).
Default is ``"local"`` so existing behaviour is preserved.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Protocol

logger = logging.getLogger(__name__)


# ── Public types ──────────────────────────────────────────────────────


# What a model's prediction looks like coming back from any backend.
# Each entity is a dict (intentionally loose) so the existing post-
# filters + offset-rebaser keep working unchanged. Shape per role:
#
#   person_ner       → {text, role, start, end, confidence, model_confidence?}
#   provenance_ner   → {text, type, start, end, confidence}
#   contents_ner     → {text, type, start, end, confidence}
#   genre_classify   → [{label, confidence}] (note: a LIST, not a single label)
#
# The HF backend normalises HF-API responses into these shapes so the
# rest of the pipeline is backend-agnostic.
Entity        = dict[str, Any]
GenrePred     = dict[str, Any]
PredictText   = Callable[[str], Awaitable[list[Entity]]]
PredictGenre  = Callable[[str, list[str]], Awaitable[list[GenrePred]]]


ExtractionMode = Literal["local", "hf-api"]


@dataclass(frozen=True)
class ModelAvailability:
    """Per-model presence report from :meth:`InferenceBackend.warm_up`.

    ``ready`` means the model loaded / is reachable; the extractor will
    actually call it. ``False`` means we gracefully fall back to no
    predictions for that role (rather than failing the whole session).
    """

    person:     bool = False
    provenance: bool = False
    contents:   bool = False
    genre:      bool = False
    # Backend-specific diagnostic strings (e.g. HF model id / load
    # source) — surfaced to the SSE stream + the UI's ModelStatusPanel.
    notes:      dict[str, str] = field(default_factory=dict)


class InferenceBackend(Protocol):
    """Anything that can run the four Stage-2 models on a text input."""

    name: ExtractionMode

    async def warm_up(self) -> ModelAvailability:
        """Load / ping every model. Called once per session at start.

        Implementations must be idempotent (a second call returns the
        cached availability without re-downloading weights).
        """
        ...

    async def person_ner(self, text: str) -> list[Entity]:
        """Run Joint Person NER. Returns BIO-aggregated entity spans."""
        ...

    async def provenance_ner(self, text: str) -> list[Entity]:
        """Run Provenance NER on a MARC 561 segment."""
        ...

    async def contents_ner(self, text: str) -> list[Entity]:
        """Run Contents NER on a MARC 505 segment."""
        ...

    async def genre_classify(self, title: str, notes: list[str]) -> list[GenrePred]:
        """Multi-label genre classification with sigmoid scores.

        Returns every label whose score crosses the model's threshold;
        the caller drops ``"other"`` and unknown labels.
        """
        ...


# ── Backend selection ─────────────────────────────────────────────────


def resolve_mode(explicit: str | None = None) -> ExtractionMode:
    """Pick the extraction mode.

    Precedence (highest first):
    1. ``explicit`` argument (passed by the router from a per-user setting).
    2. ``EXTRACTION_MODE`` env var.
    3. ``"local"`` default.
    """
    raw = (explicit or os.environ.get("EXTRACTION_MODE") or "local").strip().lower()
    if raw in ("local", "hf-api", "hf"):
        return "hf-api" if raw in ("hf-api", "hf") else "local"
    logger.warning("Unknown EXTRACTION_MODE=%r; falling back to local", raw)
    return "local"


def build_backend(
    mode: ExtractionMode,
    *,
    hf_token: str | None,
    model_overrides: dict[str, str] | None = None,
    db_session: Any | None = None,
    user_id: Any | None = None,
    skip_cache: bool = False,
) -> InferenceBackend:
    """Construct the right backend for *mode*.

    Lazy-imports so a hf-api-only deployment never imports torch and a
    local-only deployment never needs huggingface_hub at module load.

    ``db_session`` + ``user_id`` + ``skip_cache`` are plumbed into the
    HF backend's shared inference cache. When ``db_session`` is None,
    calls go through uncached as before — the local backend doesn't
    use the table since local-mode latency is dominated by torch +
    CPU, not by external I/O.
    """
    if mode == "hf-api":
        from app.pipeline.extraction_backend_hf import (  # noqa: PLC0415
            HfApiInferenceBackend,
        )
        return HfApiInferenceBackend(
            hf_token=hf_token or "",
            overrides=dict(model_overrides or {}),
            db_session=db_session,
            user_id=user_id,
            skip_cache=skip_cache,
        )
    from app.pipeline.extraction_backend_local import (  # noqa: PLC0415
        LocalInferenceBackend,
    )
    return LocalInferenceBackend(
        hf_token=hf_token,
        overrides=dict(model_overrides or {}),
    )


__all__ = [
    "Entity",
    "ExtractionMode",
    "GenrePred",
    "InferenceBackend",
    "ModelAvailability",
    "PredictGenre",
    "PredictText",
    "build_backend",
    "resolve_mode",
]
