"""Stage 2 extraction — NER + classifier inference orchestrator.

This is the web port of the desktop ``NerWorker`` in
``src/mhm_pipeline/controller/workers.py``. It runs up to four models
over a list of parsed MARC records and yields one structured event per
boundary so the FastAPI SSE consumer can stream progress to the
browser:

* Person joint NER (``alexgoldberg/hebrew-manuscript-joint-ner-v2``).
  Authoritative for PERSON spans + roles. Downloaded from the HF Hub
  with the calling user's encrypted HF token.
* Provenance NER (``ner/provenance_ner_model.pt``) — OWNER / DATE /
  COLLECTION on MARC 561. Local-only on desktop; **stubbed** here until
  we push the weights to a private HF org or container-bundle them.
* Contents NER (``ner/contents_ner_model.pt``) — WORK / FOLIO /
  WORK_AUTHOR on MARC 505. Same stub story.
* Genre classifier (``ner/genre_classifier_model.pt``) — Stage 3 P136
  fallback. Same stub story.

The same per-record post-filters that fire on desktop run here too
(``converter/authority/ner_post_filters.py``). The output JSON shape
matches ``ner_results.json`` exactly so downstream stages stay file-
coupled (Rule 48 in the desktop CLAUDE.md).

Streaming contract — events emitted in order:

* ``extraction.start`` — payload ``{"total": N}``.
* ``extraction.record.start`` — payload ``{"control_number", "index"}``.
* ``extraction.record.done`` — payload ``{"control_number", "index",
  "entity_count", "genre_count"}``.
* ``extraction.end`` — payload ``{"records_processed", "entity_total",
  "results_path"}``.
* ``extraction.error`` — payload ``{"message"}`` if anything fatal hits
  the loop. Per-record failures are logged + skipped, not fatal.

All heavy inference calls go through ``asyncio.to_thread`` so the event
loop stays responsive. Torch / transformers / huggingface-hub are
imported lazily inside thread-bound helpers; no top-level import.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from app.pipeline.extraction_backend import (
    InferenceBackend, build_backend, resolve_mode,
)

logger = logging.getLogger(__name__)


# ── Public types ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExtractionEvent:
    """One event yielded by ``extract_entities_stream``.

    Mirrors ``AgentEvent`` in ``agent_runner.py`` so the SSE formatter
    in that module can serialise either type identically.
    """

    type: str
    payload: dict


# Default model IDs / filenames. Overrides come from ``model_overrides``.
_PERSON_NER_REPO = "alexgoldberg/hebrew-manuscript-joint-ner-v2"
_PROVENANCE_MODEL_FILE = "provenance_ner_model.pt"
_CONTENTS_MODEL_FILE = "contents_ner_model.pt"
_GENRE_MODEL_FILE = "genre_classifier_model.pt"


# ── Top-level entry point ──────────────────────────────────────────────


async def extract_entities_stream(
    *,
    marc_records: list[dict[str, Any]],
    output_dir: Path,
    hf_token: str | None,
    model_overrides: dict[str, str] | None = None,
    mode: str | None = None,
    enabled_models: set[str] | None = None,
    # Cross-user inference cache plumbing (optional). When db_session
    # is supplied the HF backend reads/writes the shared
    # ``inference_cache`` table; skip_cache=True forces fresh calls.
    db_session: Any | None = None,
    user_id: Any | None = None,
    skip_cache: bool = False,
) -> AsyncIterator[ExtractionEvent]:
    """Stream NER + genre predictions for the given parsed MARC records.

    Parameters
    ----------
    marc_records
        Output of ``app.pipeline.marc_ingest.parse_marc_upload`` (or the
        equivalent rows persisted in ``run_records.marc``). One dict per
        manuscript with the desktop ``ExtractedData`` shape.
    output_dir
        Directory where ``ner_results.json`` is written on success.
        Must exist or be createable.
    hf_token
        User-scoped HuggingFace access token, unwrapped from the
        encrypted API-key store. Required for the Person joint NER load
        (HF Hub call). Pass ``None`` for stub-only runs.
    model_overrides
        Optional ``{role: identifier}`` map for testing. Keys are
        ``"person"``, ``"provenance"``, ``"contents"``, ``"genre"``.

    Yields
    ------
    ExtractionEvent
        Structured events suitable for forwarding to ``sse_stream`` in
        ``agent_runner.py``.
    """
    total = len(marc_records)
    resolved_mode = resolve_mode(mode)
    yield ExtractionEvent(
        type="extraction.start",
        payload={
            "total":            total,
            "mode":             resolved_mode,
            "person_ner_repo":  _PERSON_NER_REPO,
        },
    )

    if total == 0:
        output_path = await asyncio.to_thread(_write_results, output_dir, [])
        yield ExtractionEvent(
            type="extraction.end",
            payload={
                "records_processed": 0,
                "entity_total":      0,
                "results_path":      str(output_path),
            },
        )
        return

    # ── Build the backend (local torch / HF Inference API / Modal).
    # The warm-up call is what gates progress, so we surface it as
    # a distinct "warming" phase. Modal cold starts are 30–60 s on
    # the first call after idle; without this the UI hangs at 0%
    # with no explanation.
    backend_human = {
        "modal":   "Modal (pay-per-call serverless)",
        "hf-api":  "HuggingFace Inference Providers",
        "local":   "in-process torch",
    }.get(resolved_mode, resolved_mode)
    cold_start_hint = " — first call after idle can take 30–60s" if resolved_mode == "modal" else ""
    yield ExtractionEvent(
        type="extraction.step",
        payload={
            "phase":   "warming",
            "message": f"Contacting {backend_human}{cold_start_hint}",
        },
    )
    import time  # noqa: PLC0415
    t0 = time.monotonic()
    try:
        backend: InferenceBackend = build_backend(
            resolved_mode, hf_token=hf_token, model_overrides=model_overrides,
            db_session=db_session, user_id=user_id, skip_cache=skip_cache,
        )
        availability = await backend.warm_up()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Backend warm-up failed")
        yield ExtractionEvent(
            type="extraction.error",
            payload={"message": f"Backend warm-up failed: {exc}"},
        )
        return
    elapsed = time.monotonic() - t0
    ready_count = sum(1 for f in (availability.person, availability.provenance,
                                  availability.contents, availability.genre) if f)
    yield ExtractionEvent(
        type="extraction.step",
        payload={
            "phase":   "warmed",
            "message": f"Models ready ({ready_count}/4) in {elapsed:.1f}s",
            "elapsed_s": elapsed,
        },
    )

    # Apply the user's per-role on/off choice (from the Models
    # settings panel). Disabled roles drop to "unavailable" before
    # any inference runs, which means _process_one_record skips them.
    selected = enabled_models if enabled_models is not None else {
        "person", "provenance", "contents", "genre",
    }
    effective_availability = type(availability)(
        person=     availability.person     and ("person"     in selected),
        provenance= availability.provenance and ("provenance" in selected),
        contents=   availability.contents   and ("contents"   in selected),
        genre=      availability.genre      and ("genre"      in selected),
        notes={
            **availability.notes,
            **{r: "disabled by user"
               for r in ("person", "provenance", "contents", "genre")
               if r not in selected},
        },
    )

    # One ``extraction.model.ready`` per role so the UI's
    # ModelStatusPanel can light up four pills independently.
    for role, ready in (
        ("person",     effective_availability.person),
        ("provenance", effective_availability.provenance),
        ("contents",   effective_availability.contents),
        ("genre",      effective_availability.genre),
    ):
        yield ExtractionEvent(
            type="extraction.model.ready" if ready else "extraction.model.unavailable",
            payload={
                "model":   role,
                "ready":   ready,
                "note":    effective_availability.notes.get(role, ""),
                "backend": resolved_mode,
            },
        )

    yield ExtractionEvent(
        type="extraction.step",
        payload={
            "phase":   "processing",
            "message": f"Processing {total} record{'s' if total != 1 else ''}",
        },
    )

    results: list[dict[str, Any]] = []
    entity_total = 0
    for idx, record in enumerate(marc_records):
        cn = str(record.get("_control_number") or record.get("control_number") or f"row{idx:04d}")
        yield ExtractionEvent(
            type="extraction.record.start",
            payload={"control_number": cn, "index": idx, "total": total},
        )

        try:
            per_record = await _process_one_record(
                record, backend, enabled=selected,
            )
        except Exception as exc:  # noqa: BLE001 — never let one record stop the stream
            logger.warning("Stage 2 record %s failed: %s", cn, exc)
            per_record = {
                "_control_number":        cn,
                "text":                   "",
                "entities":               [],
                "ml_genres":              [],
                "catalog_references":    [],
                "provenance_inscriptions": [],
                "error":                  str(exc),
            }

        results.append(per_record)
        entity_total += len(per_record.get("entities") or [])

        yield ExtractionEvent(
            type="extraction.record.done",
            payload={
                "control_number": cn,
                "index":          idx,
                "total":          total,
                "entity_count":   len(per_record.get("entities") or []),
                "genre_count":    len(per_record.get("ml_genres") or []),
            },
        )

    output_path = await asyncio.to_thread(_write_results, output_dir, results)

    yield ExtractionEvent(
        type="extraction.end",
        payload={
            "records_processed": total,
            "entity_total":      entity_total,
            "results_path":      str(output_path),
        },
    )


# ── Legacy model loaders ──────────────────────────────────────────────
#
# These four helpers and the ``_PersonNer`` class below were the
# in-process model loaders before the backend abstraction landed
# (``extraction_backend.py``). The Local backend now owns model
# discovery / loading; the only thing still consumed from here is
# ``_PersonNer``, which the local backend instantiates. The other three
# loaders are kept temporarily so any external caller still importing
# them from this module gets the same behaviour. Remove in a follow-up
# once we're confident nothing external imports them.


def _load_person_pipeline(repo_id: str, hf_token: str | None) -> Any:
    """Load the joint Person NER model from a HuggingFace repo.

    The model is a token-classifier with role-aware BIO labels (v3).
    Falls back to the legacy JointModel path when the config lacks
    those labels.
    """
    import torch  # noqa: PLC0415
    from huggingface_hub import snapshot_download  # noqa: PLC0415
    from transformers import (  # noqa: PLC0415
        AutoModelForTokenClassification,
        AutoTokenizer,
    )

    # Mirror desktop's auto-device selection: MPS → CUDA → CPU.
    if torch.backends.mps.is_available():
        device = torch.device("mps")
    elif torch.cuda.is_available():
        device = torch.device("cuda")
    else:
        device = torch.device("cpu")

    # Materialise the snapshot locally so subsequent loads are offline.
    local_dir = snapshot_download(repo_id=repo_id, token=hf_token)
    tokenizer = AutoTokenizer.from_pretrained(local_dir, token=hf_token)
    model = AutoModelForTokenClassification.from_pretrained(
        local_dir, token=hf_token,
    )
    model.to(device)
    model.eval()
    id2label = {int(idx): str(label) for idx, label in model.config.id2label.items()}
    return _PersonNer(model=model, tokenizer=tokenizer, device=device, id2label=id2label)


def _maybe_load_local_ner(model_filename_or_path: str) -> Any | None:
    """Try to load a local provenance / contents .pt checkpoint.

    Returns ``None`` when the file is not on disk so callers can treat
    the model as optional. A TODO note will be appended to the run's
    output if either model is missing.

    TODO: push provenance_ner_model.pt + contents_ner_model.pt to a
    private HF org or container-bundle them so the web app has a
    working production path. For now this is a stub.
    """
    path = _resolve_local_weights(model_filename_or_path)
    if path is None:
        logger.info(
            "Local NER weights %s not found — running as stub (no entities emitted)",
            model_filename_or_path,
        )
        return None
    try:
        # Lazy import so the rest of the module loads on torch-less envs.
        from ner.ner_inference_pipeline import NERInferencePipeline  # noqa: PLC0415
        return NERInferencePipeline(model_path=str(path), device="auto")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load local NER %s: %s", path, exc)
        return None


def _maybe_load_genre_classifier(filename_or_path: str) -> Any | None:
    """Try to load the genre classifier checkpoint."""
    path = _resolve_local_weights(filename_or_path)
    if path is None:
        logger.info(
            "Genre classifier weights %s not found — running as stub",
            filename_or_path,
        )
        return None
    try:
        from converter.authority.genre_classifier import GenreClassifier  # noqa: PLC0415
        return GenreClassifier(model_path=str(path), device="auto")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load genre classifier %s: %s", path, exc)
        return None


def _resolve_local_weights(filename_or_path: str) -> Path | None:
    """Search a small set of known locations for a .pt file.

    The web backend prefers ``backend/state/models/`` so deploy artifacts
    are next to the code. Falls back to the desktop ``ner/`` directory
    in the sibling pipeline repo (dev convenience).
    """
    candidate = Path(filename_or_path)
    if candidate.is_absolute() and candidate.exists():
        return candidate

    backend_root = Path(__file__).resolve().parents[2]  # …/backend
    here_candidates = [
        backend_root / "state" / "models" / candidate.name,
        backend_root / "models" / candidate.name,
        backend_root.parent.parent / "pipeline" / "ner" / candidate.name,  # dev sibling
    ]
    for p in here_candidates:
        if p.exists():
            return p
    return None


# ── Per-record inference (called inside asyncio.to_thread) ────────────


async def _process_one_record(
    record: dict[str, Any],
    backend: InferenceBackend,
    *,
    enabled: set[str] | None = None,
) -> dict[str, Any]:
    """Async worker that runs every model on one record via *backend*.

    Mirrors the desktop ``NerWorker.run`` per-record loop. Output shape
    matches ``ner_results.json`` exactly:

    * ``_control_number``       — copied from the input record
    * ``text``                  — concatenation of every NER input
                                  (so global entity offsets land here)
    * ``entities``              — list[dict] from all three NER models
    * ``ml_genres``             — list[{"label", "confidence"}]
    * ``catalog_references``    — bibliographic citations re-routed
                                  from COLLECTION
    * ``provenance_inscriptions``— OWNER spans > 80 chars
    """
    from converter.authority.ner_post_filters import (  # noqa: PLC0415
        filter_collection_citations,
        filter_owner_length,
        filter_person_hallucinations,
        filter_work_author_folio,
    )

    cn = str(record.get("_control_number") or record.get("control_number") or "")
    all_entities: list[dict[str, Any]] = []
    en = enabled if enabled is not None else {"person", "provenance", "contents", "genre"}

    # ── 1. Person NER on notes + colophon ─────────────────────────────
    texts = _extract_person_texts(record)
    if "person" in en:
        for i, text in enumerate(texts):
            offset = _segment_offset(texts, i)
            try:
                segment_entities = await backend.person_ner(text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Person NER failed on %s: %s", cn, exc)
                continue
            for ent in segment_entities:
                _shift_offsets(ent, offset)
                ent["source"] = "person_ner"
            all_entities.extend(segment_entities)

    # ── 2. Provenance NER on MARC 561 ─────────────────────────────────
    if "provenance" in en:
        provenance_text = record.get("provenance") or ""
        if isinstance(provenance_text, str) and provenance_text.strip():
            clean = provenance_text.replace('""', '"')
            for segment in _split_pipe(clean):
                if len(segment) < 3:
                    continue
                try:
                    prov_entities = await backend.provenance_ner(segment)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Provenance NER failed on %s: %s", cn, exc)
                    continue
                for ent in prov_entities:
                    ent["source"] = "provenance_ner"
                all_entities.extend(prov_entities)

    # ── 3. Contents NER on MARC 505 ───────────────────────────────────
    if "contents" in en:
        for content in record.get("contents") or []:
            text_505 = _flatten_content(content)
            if not text_505 or len(text_505) < 5:
                continue
            try:
                cont_entities = await backend.contents_ner(text_505)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Contents NER failed on %s: %s", cn, exc)
                continue
            for ent in cont_entities:
                ent["source"] = "contents_ner"
            all_entities.extend(cont_entities)

    # ── 4. Build the full text for global offset rebasing ─────────────
    full_text = _build_full_text(texts, record)
    all_entities = _rebase_offsets(all_entities, full_text)

    # ── 5. Post-filters (mirrors desktop order) ───────────────────────
    all_entities = filter_work_author_folio(all_entities)
    all_entities, catalog_refs = filter_collection_citations(
        all_entities, surrounding_text=full_text,
    )
    all_entities, prov_inscriptions = filter_owner_length(all_entities)
    all_entities = filter_person_hallucinations(
        all_entities, surrounding_text=full_text,
    )

    # ── 6. Genre classifier (P136 fallback) ───────────────────────────
    ml_genres: list[dict[str, Any]] = []
    if "genre" in en:
        try:
            title = str(record.get("title") or "").strip()
            notes_list = [str(n) for n in (record.get("notes") or []) if n]
            predictions = await backend.genre_classify(title, notes_list)
            for item in predictions or []:
                label = str(item.get("label") or "")
                conf  = float(item.get("confidence") or 0.0)
                if not label or label == "other":
                    continue
                ml_genres.append({"label": label, "confidence": conf})
        except Exception as exc:  # noqa: BLE001
            logger.debug("Genre classifier failed on %s: %s", cn, exc)

    return {
        "_control_number":         cn,
        "text":                    full_text,
        "entities":                all_entities,
        "ml_genres":               ml_genres,
        "catalog_references":      catalog_refs,
        "provenance_inscriptions": prov_inscriptions,
    }


# ── Text-prep helpers (1:1 with desktop NerWorker) ────────────────────


def _extract_person_texts(record: dict[str, Any]) -> list[str]:
    """Pull person-NER input segments: notes + colophon text."""
    out: list[str] = []
    for note in record.get("notes") or []:
        if isinstance(note, str) and note.strip():
            out.append(note)
    colophon = record.get("colophon_text")
    if isinstance(colophon, str) and colophon.strip():
        out.append(colophon)
    return out


def _segment_offset(texts: list[str], index: int) -> int:
    offset = 0
    for i, text in enumerate(texts):
        if i >= index:
            break
        offset += len(text)
        if i < len(texts) - 1:
            offset += 1  # newline separator
    return offset


def _shift_offsets(ent: dict[str, Any], offset: int) -> None:
    s = ent.get("start")
    e = ent.get("end")
    if isinstance(s, int):
        ent["start"] = s + offset
    if isinstance(e, int):
        ent["end"] = e + offset


def _split_pipe(text: str) -> list[str]:
    return [seg.strip() for seg in text.split("|") if seg.strip()]


def _flatten_content(content: Any) -> str:
    if isinstance(content, dict):
        parts: list[str] = []
        if content.get("folio_range"):
            parts.append(f"דף {content['folio_range']}:")
        if content.get("responsibility"):
            parts.append(f"{content['responsibility']}:")
        if content.get("title"):
            parts.append(str(content["title"]))
        return " ".join(parts).strip()
    if isinstance(content, str):
        return content.strip()
    return ""


def _build_full_text(person_texts: list[str], record: dict[str, Any]) -> str:
    parts: list[str] = list(person_texts)
    prov = record.get("provenance")
    if isinstance(prov, str) and prov.strip():
        parts.append(prov.replace('""', '"'))
    for content in record.get("contents") or []:
        flat = _flatten_content(content)
        if flat:
            parts.append(flat)
    return "\n".join(parts)


def _rebase_offsets(
    entities: list[dict[str, Any]],
    full_text: str,
) -> list[dict[str, Any]]:
    """Verify each entity's (start, end) slices to its payload in
    ``full_text``; substring-search to repair where they don't."""
    out: list[dict[str, Any]] = []
    for ent in entities:
        payload = ent.get("person") or ent.get("text") or ""
        if not isinstance(payload, str) or not payload:
            continue
        s, e = ent.get("start"), ent.get("end")
        if (
            isinstance(s, int)
            and isinstance(e, int)
            and 0 <= s < e <= len(full_text)
            and full_text[s:e] == payload
        ):
            out.append(ent)
            continue
        idx = full_text.find(payload)
        if idx >= 0:
            ent["start"] = idx
            ent["end"] = idx + len(payload)
        else:
            ent["start"] = None
            ent["end"] = None
        out.append(ent)
    return out


# ── Output ────────────────────────────────────────────────────────────


def _write_results(output_dir: Path, results: list[dict[str, Any]]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "ner_results.json"
    safe = copy.deepcopy(results)
    output_path.write_text(
        json.dumps(safe, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return output_path


# ── Thin holder for the loaded HF model ───────────────────────────────


class _PersonNer:
    """Minimal wrapper around an HF token classifier with role-aware
    BIO labels (v3). Exposes ``process_text`` so the orchestrator can
    call it uniformly alongside the local-checkpoint pipelines.
    """

    def __init__(
        self,
        *,
        model: Any,
        tokenizer: Any,
        device: Any,
        id2label: dict[int, str],
    ) -> None:
        self._model = model
        self._tokenizer = tokenizer
        self._device = device
        self._id2label = id2label

    def process_text(self, text: str) -> list[dict[str, Any]]:
        """Return PERSON entities with roles encoded in the BIO tag."""
        import torch  # noqa: PLC0415

        # Import the desktop edge-normaliser when available; otherwise
        # fall back to a no-op so the function still returns sensible
        # offsets in environments where the desktop tree isn't on the
        # PYTHONPATH.
        try:
            from ner.entity_normalize import normalize_entity_text  # noqa: PLC0415
        except ImportError:  # pragma: no cover
            def normalize_entity_text(s: str) -> str:
                return s.strip()

        tokens = text.split()
        if not tokens:
            return []

        enc = self._tokenizer(
            tokens,
            is_split_into_words=True,
            return_tensors="pt",
            truncation=True,
            max_length=256,
        )
        input_ids = enc["input_ids"].to(self._device)
        attention_mask = enc["attention_mask"].to(self._device)

        with torch.no_grad():
            outputs = self._model(input_ids=input_ids, attention_mask=attention_mask)

        probs = torch.softmax(outputs.logits[0], dim=-1)
        pred_ids = torch.argmax(outputs.logits[0], dim=-1).cpu().tolist()
        max_probs = probs.max(dim=-1).values.cpu().tolist()

        word_ids = enc.word_ids(batch_index=0)
        tags: list[str] = []
        tag_probs: list[float] = []
        prev_word = None
        for idx, w in enumerate(word_ids):
            if w is None:
                continue
            if w != prev_word:
                tags.append(self._id2label.get(int(pred_ids[idx]), "O"))
                tag_probs.append(float(max_probs[idx]))
            prev_word = w

        token_offsets = _token_offsets(text, tokens)
        out: list[dict[str, Any]] = []
        cur_tokens: list[str] = []
        cur_probs: list[float] = []
        cur_start: int | None = None
        cur_role: str | None = None

        def _flush(end_idx: int) -> None:
            nonlocal cur_tokens, cur_probs, cur_start, cur_role
            if cur_start is None or cur_role is None or not cur_tokens:
                cur_tokens = []
                cur_probs = []
                cur_start = None
                cur_role = None
                return
            start_char = token_offsets[cur_start][0]
            end_char = token_offsets[end_idx - 1][1]
            raw = text[start_char:end_char]
            cleaned = normalize_entity_text(raw)
            if cleaned:
                shift = raw.find(cleaned)
                if shift >= 0:
                    start = start_char + shift
                    end = start + len(cleaned)
                    confidence = round(sum(cur_probs) / len(cur_probs), 4)
                    out.append({
                        "person":           cleaned,
                        "role":             cur_role,
                        "confidence":       confidence,
                        "model_confidence": confidence,
                        "start":            start,
                        "end":              end,
                    })
            cur_tokens = []
            cur_probs = []
            cur_start = None
            cur_role = None

        for idx, (tok, tag, prob) in enumerate(zip(tokens, tags, tag_probs)):
            if tag.startswith("B-"):
                _flush(idx)
                cur_tokens = [tok]
                cur_probs = [prob]
                cur_start = idx
                cur_role = tag[2:]
            elif tag.startswith("I-"):
                role = tag[2:]
                if cur_start is None or cur_role != role:
                    _flush(idx)
                    cur_tokens = [tok]
                    cur_probs = [prob]
                    cur_start = idx
                    cur_role = role
                else:
                    cur_tokens.append(tok)
                    cur_probs.append(prob)
            else:
                _flush(idx)

        _flush(len(tokens))
        return out


def _token_offsets(text: str, tokens: list[str]) -> list[tuple[int, int]]:
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for tok in tokens:
        idx = text.find(tok, cursor)
        if idx < 0:
            # Defensive — tokenisation drift. Fall back to a synthetic
            # offset so the loop doesn't crash.
            offsets.append((cursor, cursor + len(tok)))
            cursor = cursor + len(tok)
        else:
            offsets.append((idx, idx + len(tok)))
            cursor = idx + len(tok)
    return offsets


__all__ = ["ExtractionEvent", "extract_entities_stream"]
