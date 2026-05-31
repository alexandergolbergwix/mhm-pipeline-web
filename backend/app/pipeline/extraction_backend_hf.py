"""HuggingFace Inference Providers backend (Mode B).

Calls HF's serverless inference layer over HTTPS — the backend never
loads model weights into its own process. Net effect: deploy image
shrinks ~2 GB, no torch needed in production. See plan in
``docs/project-hierarchy-plan.md`` (Phase B migration).

Behaviour:
* Lazy ``InferenceClient`` construction inside ``warm_up`` so the
  module imports clean on a torch-only deploy that never selects this
  backend.
* 503 ``model_loading`` responses (cold start) trigger one retry after
  the suggested ``estimated_time`` seconds, up to 3 total attempts.
  Surfaces ``model.warming`` / ``model.ready`` lifecycle events the
  caller can re-broadcast to the SSE stream.
* 429 ``too_many_requests`` triggers exponential backoff, 3 retries.
* Output normalised to the same Entity / GenrePred shape the local
  backend emits, so the rest of the pipeline is backend-agnostic.

Three of our four models are not yet on the HF Hub (Provenance NER,
Contents NER, Genre classifier are local ``.pt`` files). When a model
id is missing, the corresponding method gracefully returns ``[]`` so
the session still produces useful Person-NER output. Push the .pt
files via ``scripts/push_to_hf/*.py`` to unlock the other three.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from app.pipeline.extraction_backend import (
    Entity, GenrePred, InferenceBackend, ModelAvailability,
)

logger = logging.getLogger(__name__)


# Canonical model ids. Override via the ``overrides`` constructor arg
# or env vars (``HF_PERSON_NER_REPO``, ``HF_PROVENANCE_NER_REPO``, …).
#
# Provenance + Contents are published as CUSTOM-CODE repos (their head
# is a 2-layer Linear-ReLU-Linear that doesn't fit stock
# BertForTokenClassification). They're not deployable to HF's
# serverless Inference Providers tier; we leave their IDs empty so
# this backend gracefully returns ``[]`` and the web app keeps loading
# the local .pt files for those two roles. Set the env var to opt
# back in if you've configured a dedicated Inference Endpoint.
_DEFAULT_PERSON_REPO     = "alexgoldberg/hebrew-manuscript-joint-ner-v2"
_DEFAULT_PROVENANCE_REPO = ""   # custom-code; not on Inference Providers
_DEFAULT_CONTENTS_REPO   = ""   # custom-code; not on Inference Providers
_DEFAULT_GENRE_REPO      = "alexgoldberg/hebrew-manuscript-genre-classifier"


# Retry + timeout defaults. ``estimated_time`` in 503 bodies tells us
# how long HF expects the cold start to take — we use it directly,
# clamped to a sane band.
_MAX_RETRIES         = 3
_BACKOFF_BASE_SEC    = 2.0
_COLD_START_MIN_SEC  = 5.0
_COLD_START_MAX_SEC  = 30.0


@dataclass
class _ModelSlot:
    """Per-role config + lazy InferenceClient."""

    role:        str
    repo_id:     str
    task:        str        # "token-classification" | "text-classification"
    available:   bool = False
    last_error:  str  = ""


class HfApiInferenceBackend(InferenceBackend):
    """HuggingFace Inference Providers backend."""

    name = "hf-api"

    def __init__(
        self, *,
        hf_token: str,
        overrides: dict[str, str] | None = None,
    ) -> None:
        import os  # noqa: PLC0415
        ov = overrides or {}
        self._token = hf_token
        self._slots: dict[str, _ModelSlot] = {
            "person":     _ModelSlot(
                role="person",     task="token-classification",
                repo_id=ov.get("person") or os.environ.get("HF_PERSON_NER_REPO", "") or _DEFAULT_PERSON_REPO,
            ),
            "provenance": _ModelSlot(
                role="provenance", task="token-classification",
                repo_id=ov.get("provenance") or os.environ.get("HF_PROVENANCE_NER_REPO", "") or _DEFAULT_PROVENANCE_REPO,
            ),
            "contents":   _ModelSlot(
                role="contents",   task="token-classification",
                repo_id=ov.get("contents") or os.environ.get("HF_CONTENTS_NER_REPO", "") or _DEFAULT_CONTENTS_REPO,
            ),
            "genre":      _ModelSlot(
                role="genre",      task="text-classification",
                repo_id=ov.get("genre") or os.environ.get("HF_GENRE_REPO", "") or _DEFAULT_GENRE_REPO,
            ),
        }
        self._client: Any | None = None
        self._availability: ModelAvailability | None = None

    # ── warm_up ────────────────────────────────────────────────────────

    async def warm_up(self) -> ModelAvailability:
        if self._availability is not None:
            return self._availability
        if not self._token:
            raise ValueError(
                "HuggingFace token is required for hf-api mode. "
                "Add one in Settings → Credentials.",
            )
        # Lazy import: keeps the module clean on torch-only deploys.
        from huggingface_hub import InferenceClient  # noqa: PLC0415
        self._client = InferenceClient(token=self._token, timeout=60.0)

        notes: dict[str, str] = {}
        for role, slot in self._slots.items():
            if not slot.repo_id:
                notes[role] = "no model id configured (push to HF first)"
                slot.available = False
                continue
            # Mark available without calling — we discover cold-start
            # state on first real call. A pre-warm hit per model would
            # 4x the perceived startup cost without changing outcomes.
            slot.available = True
            notes[role] = slot.repo_id

        self._availability = ModelAvailability(
            person=self._slots["person"].available,
            provenance=self._slots["provenance"].available,
            contents=self._slots["contents"].available,
            genre=self._slots["genre"].available,
            notes=notes,
        )
        return self._availability

    # ── Per-role calls ─────────────────────────────────────────────────

    async def person_ner(self, text: str) -> list[Entity]:
        return await self._token_classify("person", text)

    async def provenance_ner(self, text: str) -> list[Entity]:
        return await self._token_classify("provenance", text)

    async def contents_ner(self, text: str) -> list[Entity]:
        return await self._token_classify("contents", text)

    async def genre_classify(self, title: str, notes: list[str]) -> list[GenrePred]:
        slot = self._slots["genre"]
        if not slot.available or self._client is None:
            return []
        # Genre classifier sees the title + the notes joined.
        joined = "\n".join([title.strip()] + [str(n).strip() for n in notes if n])
        try:
            raw = await self._invoke(slot, joined, kind="text-classification")
        except _HfRequestFailed as exc:
            slot.last_error = str(exc); slot.available = False
            logger.warning("HF genre_classify failed (%s); disabling: %s", slot.repo_id, exc)
            return []
        return _normalise_text_classification(raw)

    # ── Internal: token-classification with retries ────────────────────

    async def _token_classify(self, role: str, text: str) -> list[Entity]:
        slot = self._slots[role]
        if not slot.available or self._client is None or not text.strip():
            return []
        try:
            raw = await self._invoke(slot, text, kind="token-classification")
        except _HfRequestFailed as exc:
            slot.last_error = str(exc); slot.available = False
            logger.warning(
                "HF %s_ner failed (%s); disabling for this session: %s",
                role, slot.repo_id, exc,
            )
            return []
        return _normalise_token_classification(raw, role=role)

    async def _invoke(
        self, slot: _ModelSlot, text: str, *,
        kind: str,
    ) -> Any:
        """Call HF's serverless inference; retry on 503 cold-start."""
        assert self._client is not None
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                if kind == "token-classification":
                    out = await asyncio.to_thread(
                        self._client.token_classification,
                        text, model=slot.repo_id,
                    )
                else:
                    out = await asyncio.to_thread(
                        self._client.text_classification,
                        text, model=slot.repo_id,
                    )
                return out
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                est = _estimated_cold_start(exc)
                if est is not None and attempt < _MAX_RETRIES - 1:
                    wait = max(_COLD_START_MIN_SEC,
                                min(_COLD_START_MAX_SEC, est + 1.0))
                    logger.info("HF %s warming up; retry in %.1fs (attempt %d)",
                                 slot.role, wait, attempt + 1)
                    await asyncio.sleep(wait)
                    continue
                if _is_rate_limited(exc) and attempt < _MAX_RETRIES - 1:
                    backoff = _BACKOFF_BASE_SEC * (2 ** attempt)
                    logger.info("HF %s rate-limited; backoff %.1fs", slot.role, backoff)
                    await asyncio.sleep(backoff)
                    continue
                # Permanent error or attempts exhausted.
                raise _HfRequestFailed(str(exc)) from exc
        raise _HfRequestFailed(f"max retries exhausted ({last_exc})")


# ── Error introspection helpers ───────────────────────────────────────


class _HfRequestFailed(RuntimeError):
    """Raised after retries are exhausted or on a non-retriable error."""


_COLD_START_RE = re.compile(
    r"(model is currently loading|estimated_time|503|model_loading|is currently being deployed)",
    re.IGNORECASE,
)
_RATE_LIMIT_RE = re.compile(r"(429|too many requests|rate.?limit)", re.IGNORECASE)
_EST_TIME_RE   = re.compile(r"estimated[_ ]time[^0-9]*([0-9.]+)", re.IGNORECASE)


def _estimated_cold_start(exc: Exception) -> float | None:
    """Return the wait-time in seconds suggested by HF for a 503, else None."""
    msg = str(exc)
    if not _COLD_START_RE.search(msg):
        return None
    m = _EST_TIME_RE.search(msg)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return _COLD_START_MIN_SEC


def _is_rate_limited(exc: Exception) -> bool:
    return bool(_RATE_LIMIT_RE.search(str(exc)))


# ── Output normalisation ─────────────────────────────────────────────


def _normalise_token_classification(
    raw: Any, *, role: str,
) -> list[Entity]:
    """Convert HF token-classification output to our Entity shape.

    HF returns a list of:
        {entity_group: "PERSON" | "OWNER" | …,
         score: float,
         word: str,
         start: int, end: int}

    Our pipeline expects (per the desktop NerWorker shape):
        {text, role/type, start, end, confidence, source}
    """
    out: list[Entity] = []
    if not isinstance(raw, list):
        return out
    label_field = "role" if role == "person" else "type"
    for item in raw:
        if not isinstance(item, dict):
            continue
        ent: Entity = {
            "text":       str(item.get("word") or "").strip(),
            label_field:  str(item.get("entity_group") or item.get("entity") or "").upper(),
            "start":      int(item.get("start") or 0),
            "end":        int(item.get("end") or 0),
            "confidence": float(item.get("score") or 0.0),
        }
        # Person NER on the desktop also emits ``model_confidence``;
        # HF returns one score so we set both to the same value.
        if role == "person":
            ent["model_confidence"] = ent["confidence"]
        if ent["text"] and ent.get(label_field):
            out.append(ent)
    return out


def _normalise_text_classification(raw: Any) -> list[GenrePred]:
    """Convert HF text-classification output to genre predictions.

    HF returns either a single dict or a list of dicts:
        {label: str, score: float}

    We pass through every label (the caller drops ``"other"``).
    """
    out: list[GenrePred] = []
    items = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        out.append({"label": label, "confidence": float(item.get("score") or 0.0)})
    return out


__all__ = ["HfApiInferenceBackend"]
