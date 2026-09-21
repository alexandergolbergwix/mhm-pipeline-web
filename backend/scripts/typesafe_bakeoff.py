#!/usr/bin/env python3
"""Phase 0 bake-off: TypeSafe Jev (System One) vs an eval-agent tier-1 judge.

Measurement-only and read-only. The script rebuilds the same verify inputs the
production channels build (NER entities, HMO Studio items, Wikidata Studio
items), judges the SAME fixture twice — once with an eval-agent tier-1 model
(subprocess, Qubrid/Gemini) and once with TypeSafe Jev (typed Choice questions
per rubric axis) — and reports per-axis agreement, latency, and cost. Nothing
is written back to Postgres, caches, or the live wiki.

Jev input parity: the ``state`` is the byte-identical tier-1 prompt produced by
the evaluator's ``build_prompt`` (rubric + prediction + MARC context +
grounding + skill block), with only the trailing "Return only the JSON
verdict." line replaced by a Jev-specific instruction. The ``overall`` verdict
is computed in code from the rubrics' universal table, not asked of the model.

Env
---
- ``DATABASE_URL`` — Postgres (Heroku prod or local). Read-only here.
- ``TYPESAFE_API_KEY`` — falls back to
  ``heroku config:get TYPESAFE_API_KEY -a mhm-pipeline-web``.
- ``QUBRID_API_KEY`` / ``GEMINI_API_KEY`` — for the tier-1 baseline judge.
- A repo-root ``.env`` is loaded when present.

Examples
--------
25 HMO items, Jev vs Qubrid Kimi K2.5::

    cd backend && DATABASE_URL=... .venv/bin/python -m scripts.typesafe_bakeoff \\
        --channel hmo --limit 25

Person NER bake-off::

    python -m scripts.typesafe_bakeoff --channel ner --evaluator person_ner --limit 50
"""

from __future__ import annotations

import argparse
import asyncio
import html as _html
import json
import os
import statistics
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import session_scope  # noqa: E402

DEFAULT_RUN_ID = "48ba6c13-115c-4763-bff1-c08b9031b518"
DEFAULT_MODEL = "jev-1.13.0"
DEFAULT_TIER_MODEL = "moonshotai/Kimi-K2.5"
PRICE_PER_MTOK = 0.042  # $42/Btok (TypeSafe public list); console-verified
# A blocking 'no' below this confidence is uncertainty, not a defect: gate it
# to 'partial' (curator review) instead of 'fail'. Calibrated against the
# 2026-09-20 bake-off (all wikidata role_ok=false-positives sat at conf<0.25).
ROLE_CONF_GATE = 0.5
TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
THRESHOLD = -1.0
NAXIS, TAXIS, RAXIS = "name_ok", "type_ok", "role_ok"

_JEV_CLOSE = (
    "Do not produce a JSON verdict object. Instead, answer the questions "
    "attached to this request: each question is one axis of the rubric's "
    "output contract, applied to the prediction and MARC context above."
)


def _load_dotenv() -> None:
    env_path = _REPO / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _fetch_heroku_secret(name: str) -> str:
    out = subprocess.run(
        ["heroku", "config:get", name, "-a", "mhm-pipeline-web"],
        capture_output=True, text=True, check=False, timeout=30,
    )
    val = (out.stdout or "").strip()
    return val if val and not val.startswith("Error") else ""


def _resolve_typesafe_key() -> str:
    val = os.environ.get("TYPESAFE_API_KEY", "").strip()
    return val or _fetch_heroku_secret("TYPESAFE_API_KEY")


def _ensure_eval_agent_on_path() -> None:
    from app.pipeline.agent_runner import locate_eval_agent

    root = str(locate_eval_agent())
    if root not in sys.path:
        sys.path.insert(0, root)


# ── channel builds (read-only, mirror local_measure_verify) ──────────────
async def _build_ner(
    run_id: uuid.UUID, evaluator: str, max_predictions: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from app.models.extraction_approval import ExtractionApproval
    from app.pipeline.marc_verify_context import load_run_marc_records_scoped
    from app.routers.extraction_verify import _approval_to_ner_shape

    genre = evaluator == "genre_classifier"
    sources = ("genre", "genre_ml") if genre else (evaluator,)
    async with session_scope() as db:
        rows = (await db.execute(
            select(ExtractionApproval)
            .where(ExtractionApproval.run_id == run_id)
            .where(ExtractionApproval.source.in_(sources))
            .order_by(
                ExtractionApproval.control_number,
                ExtractionApproval.source,
                ExtractionApproval.text,
            )
        )).scalars().all()

    # Scope first: keep only the control numbers that cover the first
    # `max_predictions` entities, then load MARC just for those CNs (the
    # run's full MARC JSONB is slow to pull — Rule W-246).
    per_cn: dict[str, list[Any]] = {}
    for r in rows:
        per_cn.setdefault(r.control_number, []).append(r)
    selected_cns: list[str] = []
    kept = 0
    for cn, cn_rows in per_cn.items():
        selected_cns.append(cn)
        kept += len(cn_rows)
        if max_predictions and kept >= max_predictions:
            break
    if max_predictions and len(selected_cns) < len(per_cn):
        print(f"[build] scoped to first {len(selected_cns)} control "
              f"number(s) covering {kept} prediction(s)")
    marc_records = []
    async with session_scope() as db:
        marc_records = await load_run_marc_records_scoped(
            db, run_id, set(selected_cns),
        )
    marc_by_cn = {
        str(rec.get("_control_number") or ""): rec for rec in marc_records
    }

    ents_by_cn: dict[str, list[dict[str, Any]]] = {}
    genres_by_cn: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r.control_number not in set(selected_cns):
            continue
        if r.source in ("genre", "genre_ml"):
            if not genre:
                continue
            genres_by_cn.setdefault(r.control_number, []).append({
                "label": r.text,
                "confidence": float(r.model_confidence or r.confidence or 0.0),
                "_entity_id": str(r.id),
            })
        elif genre:
            continue
        else:
            ents_by_cn.setdefault(r.control_number, []).append(
                _approval_to_ner_shape(r),
            )
    ner_records = [
        {
            "_control_number": cn,
            "text": str((marc_by_cn.get(cn) or {}).get("text") or ""),
            "entities": ents_by_cn.get(cn, []),
            "ml_genres": genres_by_cn.get(cn, []),
        }
        for cn in selected_cns
    ]
    if not ner_records:
        raise SystemExit(f"run {run_id} has no extraction rows for {evaluator}")
    print(f"[build] NER: {len(ner_records)} record(s), "
          f"{sum(len(r['entities']) + len(r['ml_genres']) for r in ner_records)} "
          f"prediction(s) for {evaluator}")
    return ner_records, marc_records


async def _build_hmo(run_id: uuid.UUID, scratch: Path) -> tuple[list, list]:
    from fastapi.concurrency import run_in_threadpool

    from app.models.extraction_approval import ExtractionApproval
    from app.models.run import AuthorityMatch, RdfTripleOverride, RunRecord
    from app.pipeline import hmo_item_build
    from app.pipeline.hmo_item_shacl import build_shacl_report_for_items
    from app.pipeline.rdf_build import (
        RdfBuildOptions,
        build_rdf_graph,
        normalise_matches,
    )
    from converter.wikibase.hmo_exporter import (
        HmoWikibaseExporter,
        resolve_against_mappings,
    )

    async with session_scope() as db:
        records = (await db.execute(
            select(RunRecord).where(RunRecord.run_id == run_id)
            .order_by(RunRecord.control_number.asc())
        )).scalars().all()
        if not records:
            raise SystemExit(f"run {run_id} has no MARC records")
        matches = (await db.execute(
            select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
            .where(AuthorityMatch.approved.is_(True))
        )).scalars().all()
        ner_rows = (await db.execute(
            select(ExtractionApproval).where(ExtractionApproval.run_id == run_id)
            .where(ExtractionApproval.approved.is_(True))
        )).scalars().all()
        overrides_rows = (await db.execute(
            select(RdfTripleOverride).where(RdfTripleOverride.run_id == run_id)
        )).scalars().all()
        schema_mappings = await hmo_item_build._load_schema_mappings(db)

        marc_records = [dict(r.marc) for r in records]
        authority_matches = normalise_matches(matches)
        entities_by_cn: dict[str, list[dict[str, Any]]] = {}
        for r in ner_rows:
            entities_by_cn.setdefault(r.control_number, []).append({
                "text": r.override_text or r.text,
                "type": (r.override_type or r.type or "").upper(),
                "role": (r.override_role or r.role or "").upper(),
                "source": r.source,
                "start": int(r.start or 0),
                "end": int(r.end or 0),
                "confidence": r.confidence,
                "model_confidence": r.model_confidence,
            })
        kima_places_by_cn: dict[str, dict[str, str]] = {}
        for rec in marc_records:
            cn = str(rec.get("_control_number") or rec.get("control_number") or "")
            kp = rec.get("kima_places")
            if cn and isinstance(kp, dict) and kp:
                kima_places_by_cn[cn.strip("\"'")] = kp
        overrides = [{
            "subject_uri": r.subject_uri,
            "predicate_uri": r.predicate_uri,
            "new_value": r.new_value,
            "new_datatype": r.new_datatype,
            "new_lang": r.new_lang,
        } for r in overrides_rows]

    ttl_path = scratch / "manuscripts.ttl"
    await build_rdf_graph(
        marc_records=marc_records,
        authority_matches=authority_matches,
        entities_by_cn=entities_by_cn,
        output_path=ttl_path,
        overrides=overrides,
        kima_places_by_cn=kima_places_by_cn,
        build_options=RdfBuildOptions(
            add_epistemological_status=True,
            add_cataloging_view=True,
            add_philological_overlay=True,
        ),
    )
    drafts = await run_in_threadpool(HmoWikibaseExporter().from_ttl, ttl_path)
    # The export quality gate (assert_export_quality) is an upload-time gate;
    # a measurement rebuild may trip it on drifted data (e.g. blank-node
    # AnthologyPositions in the current code). Judges can still measure on the
    # raw drafts, so warn and continue instead of failing the bake-off.
    resolved = await run_in_threadpool(resolve_against_mappings, drafts, schema_mappings)
    resolved_dicts = [e.to_dict() for e in resolved]
    shacl_report = await build_shacl_report_for_items(ttl_path, resolved_dicts)
    items = []
    for e in resolved_dicts:
        lid = str(e.get("local_id") or "")
        items.append({**e, "shacl_issues": shacl_report.get(lid) or []})
    print(f"[build] HMO items: {len(items)} entities (export quality gate skipped)")
    return items, marc_records


async def _build_wikidata(run_id: uuid.UUID) -> tuple[list, list]:
    from app.models.extraction_approval import ExtractionApproval
    from app.models.run import AuthorityMatch, RunRecord
    from app.pipeline import wikidata_studio

    async with session_scope() as db:
        records = (await db.execute(
            select(RunRecord).where(RunRecord.run_id == run_id)
            .order_by(RunRecord.control_number.asc())
        )).scalars().all()
        if not records:
            raise SystemExit(f"run {run_id} has no MARC records")
        matches = (await db.execute(
            select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
            .where(AuthorityMatch.approved.is_(True))
        )).scalars().all()
        ner_rows = (await db.execute(
            select(ExtractionApproval).where(ExtractionApproval.run_id == run_id)
            .where(ExtractionApproval.approved.is_(True))
        )).scalars().all()
        marc_records = [dict(r.marc) for r in records]
        approved_matches = [{
            "id": str(m.id),
            "control_number": m.control_number,
            "entity_text": m.entity_text,
            "entity_kind": m.entity_kind,
            "role": m.role,
            "field": (m.payload or {}).get("field") or "",
            "matched_name": m.matched_name,
            "mazal_id": m.mazal_id,
            "viaf_id": m.viaf_id,
            "wikidata_qid": m.wikidata_qid,
            "confidence": m.confidence,
            "source": m.source,
            "payload": m.payload or {},
        } for m in matches]
        entities_by_cn: dict[str, list[dict[str, Any]]] = {}
        for r in ner_rows:
            entities_by_cn.setdefault(r.control_number, []).append({
                "text": r.override_text or r.text,
                "type": (r.override_type or r.type or "").upper(),
                "role": (r.override_role or r.role or "").upper(),
                "source": r.source,
                "start": int(r.start or 0),
                "end": int(r.end or 0),
                "confidence": r.confidence,
                "model_confidence": r.model_confidence,
            })
        hmo_instance_qids = await wikidata_studio.hmo_instance_qids_for_run(
            db,
            run_id,
            [str(record.control_number) for record in records],
        )

    built = await wikidata_studio.build_items_for_run(
        marc_records=marc_records,
        approved_matches=approved_matches,
        entities_by_cn=entities_by_cn,
        hmo_instance_qids=hmo_instance_qids,
    )
    items = list(built.get("items") or [])
    for it in items:
        it.setdefault("local_id", wikidata_studio.local_id_for_item(it))
    print(f"[build] Wikidata items: {len(items)} entities")
    return items, marc_records


# ── fixture + tier-1 baseline ─────────────────────────────────────────────
_STUDIO_FILES = {
    "hmo": "hmo_wikibase_items.json",
    "wikidata": "wikidata_items.json",
}


def _write_fixture(
    pipeline_dir: Path,
    channel: str,
    items: list[dict[str, Any]],
    marc_records: list[dict[str, Any]],
) -> None:
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    (pipeline_dir / "marc_extracted.json").write_text(
        json.dumps(marc_records, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    name = _STUDIO_FILES.get(channel, "ner_results.json")
    (pipeline_dir / name).write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def _run_tier1_baseline(
    *, pipeline_dir: Path, state_dir: Path, evaluator: str,
    tier_model: str, rpm: int,
) -> tuple[dict[str, dict[str, Any]], int]:
    """Run eval-agent over the fixture; return ``{key: verdict_row}`` + tokens."""
    from app.pipeline.agent_runner import _python_for, locate_eval_agent

    root = locate_eval_agent()
    py = _python_for(root)
    cmd = [
        py, "-m", "eval_agent.cli", "run",
        "--pipeline-output", str(pipeline_dir),
        "--evaluators", evaluator,
        "--linear", "--no-cache", "--no-self-verify",
        "--threshold", "-1",
        "--tier-model", tier_model,
        "--rpm", str(rpm),
        "--state-dir", str(state_dir),
    ]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.run(
        cmd, cwd=str(root), env=env, capture_output=True, text=True,
        timeout=7200, check=False,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        raise RuntimeError(f"eval-agent exited {proc.returncode}: {tail}")

    runs_dir = state_dir / "runs"
    run_dirs = sorted(p for p in runs_dir.iterdir() if p.is_dir()) if runs_dir.is_dir() else []
    if not run_dirs:
        raise RuntimeError("eval-agent produced no run artefacts")
    return _read_results_jsonl(run_dirs[-1] / "results.jsonl")


def _load_tier1_results(state_dir: Path) -> tuple[dict[str, dict[str, Any]], int]:
    """Load verdicts from the most complete prior eval-state run."""
    runs_dir = state_dir / "runs"
    run_dirs = sorted(p for p in runs_dir.iterdir() if p.is_dir()) if runs_dir.is_dir() else []
    if not run_dirs:
        raise SystemExit(f"--tier1-reuse: no prior eval-state runs under {state_dir}")
    counts = []
    for d in run_dirs:
        rp = d / "results.jsonl"
        n = 0
        if rp.is_file():
            n = sum(1 for line in rp.read_text(encoding="utf-8").splitlines() if line.strip())
        counts.append((n, d))
    counts.sort(key=lambda t: (-t[0], t[1].name))
    best = counts[0]
    if not best[0]:
        raise SystemExit(f"--tier1-reuse: no results.jsonl rows under {state_dir}")
    print(f"[tier-1] reusing {best[0]} verdict(s) from {best[1].name}")
    return _read_results_jsonl(best[1] / "results.jsonl")


def _read_results_jsonl(results_path: Path) -> tuple[dict[str, dict[str, Any]], int]:
    verdicts: dict[str, dict[str, Any]] = {}
    tokens = 0
    for line in results_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        cand = row.get("candidate") if isinstance(row.get("candidate"), dict) else {}
        key = str(
            cand.get("_local_id") or cand.get("_entity_id")
            or cand.get("local_id") or row.get("record_id") or "",
        )
        if key:
            verdicts[key] = row
        usage = row.get("usage") or {}
        tokens += int(usage.get("input_tokens") or 0) + int(usage.get("total_tokens") or 0)
    return verdicts, tokens


def _load_fixture_records(channel: str, pipeline_dir: Path) -> list[dict[str, Any]]:
    path = pipeline_dir / _STUDIO_FILES.get(channel, "ner_results.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _pairing_and_candidates(
    evaluator: Any,
    records: list[dict[str, Any]],
    marc_records: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], Any]]:
    """Mirror eval-agent session.execute(): merge MARC across control numbers."""
    from eval_agent.evaluators import (
        HMO_WIKIBASE_ITEM_EVALUATORS,
        WIKIDATA_ITEM_EVALUATORS,
    )
    from eval_agent.ingest import marc_extract

    marc_index = marc_extract.index_by_id(marc_records)
    is_hmo = evaluator.id in HMO_WIKIBASE_ITEM_EVALUATORS
    is_wiki = evaluator.id in WIKIDATA_ITEM_EVALUATORS
    out: list[tuple[dict[str, Any], Any]] = []
    for rec in records:
        if is_wiki:
            from eval_agent.ingest import wikidata_items

            rid = marc_extract.canonical_control_number(
                wikidata_items.control_number(rec),
            )
            cns = [marc_extract.canonical_control_number(cn)
                   for cn in wikidata_items.control_numbers(rec)]
        elif is_hmo:
            from eval_agent.ingest import hmo_wikibase_items

            rid = marc_extract.canonical_control_number(
                hmo_wikibase_items.control_number(rec),
            )
            cns = [marc_extract.canonical_control_number(cn)
                   for cn in hmo_wikibase_items.control_numbers(rec)]
        else:
            rid = marc_extract.canonical_control_number(rec.get("_control_number"))
            cns = [rid] if rid else []
        marc_rec = marc_index.get(rid, {})
        if (is_wiki or is_hmo) and cns:
            primary = rid or cns[0]
            marc_rec = marc_extract.merge_records(
                [marc_index[cn] for cn in cns if cn in marc_index],
                primary=marc_index.get(primary),
            )
        if (is_wiki or is_hmo) and not marc_rec and len(marc_records) == 1:
            marc_rec = marc_records[0]
        for cand in evaluator.extract_candidates(
            ner_record=rec, marc_record=marc_rec, threshold=THRESHOLD,
        ):
            out.append((rec, cand))
    return out


def _candidate_key(evaluator_id: str, payload: dict[str, Any]) -> str:
    if evaluator_id in ("hmo_wikibase_item", "wikidata_item"):
        return str(payload.get("_local_id") or "")
    return str(payload.get("_entity_id") or payload.get("id") or "")


# ── Jev question sets ─────────────────────────────────────────────────────
_NAME_OK = (
    "Applying the evaluation brief in the state — including its per-entity-type "
    "and label-quality exceptions (system labels, controlled-vocabulary terms, "
    "subject headings, honest-negative descriptions) — is the predicted "
    "span/label acceptable as the identity of a real entity? 'yes' = the brief "
    "declares the label complete (exact/vowelized MARC match, or an accepted "
    "system/controlled-vocabulary label with substance); 'partial' = "
    "trimmed/extended or weakened label; 'no' = absent, a different entity, "
    "generic-placeholder-only, or malformed. Boundary noise is 'partial', not "
    "exact: a span that cuts a bracket/parenthesis mid-way (unclosed '(' or an "
    "unterminated title) or appends a parenthetical not part of the entity. An "
    "empty MARC context with no accepted-exception coverage forces 'no'."
)
_TYPE_OK = (
    "Applying the evaluation brief in the state, is the predicted entity type "
    "correct for this span/label? 'yes' = clearly correct; 'partial' = correct "
    "but truncated/ambiguous, or the relationship is different than claimed — "
    "e.g. in a provenance note 'בן/בכמה\"ר <name>' names a father or teacher, "
    "not the owner/acquirer; 'no' = clearly the wrong kind of thing."
)
_ROLE_OK_PERSON = (
    "Applying the evaluation brief in the state, does the MARC evidence "
    "support the predicted role for this person? 'yes' = the role-mapped "
    "field(s) support it; 'partial' = adjacent but weaker evidence; "
    "'no' = MARC unambiguously assigns a different role. A dictation note "
    "(מכתיבת יד / 'dictated to…') records the author or dictator — it does "
    "not make the named person a copyist (TRANSCRIBER). Honor the "
    "deterministic grounding signal stated in the state."
)
_EVIDENCE_FIELDS = {
    "person_ner": {
        "authors": "authors list", "contributors": "contributors list",
        "colophon_text": "colophon text", "data_from_colophon": "colophon data",
        "provenance": "provenance", "notes": "notes", "title": "title",
        "none": "no evidence anywhere",
    },
    "contents_ner": {
        "contents": "contents / table of contents", "notes": "notes",
        "colophon_text": "colophon text", "title": "title",
        "authors": "authors", "contributors": "contributors", "subjects": "subjects",
        "none": "no evidence anywhere",
    },
    "provenance_ner": {
        "provenance": "provenance", "notes": "notes", "colophon_text": "colophon text",
        "subjects": "subjects", "contributors": "contributors", "title": "title",
        "none": "no evidence anywhere",
    },
    "genre_classifier": {
        "genres": "MARC 655 genre field", "subjects": "subject headings",
        "notes": "notes", "title": "title", "variant_titles": "variant titles",
        "none": "no evidence anywhere",
    },
    "hmo_wikibase_item": {
        "title": "MARC title", "authors": "authors", "contributors": "contributors",
        "subjects": "subjects", "provenance": "provenance", "notes": "notes",
        "colophon_text": "colophon text", "contents": "contents", "dates": "dates",
        "place": "place", "shelfmark": "shelfmark",
        "claims": "the item's own claims",
        "descriptions": "the item's own descriptions",
        "none": "no evidence anywhere",
    },
    "wikidata_item": {
        "title": "MARC title", "authors": "authors", "contributors": "contributors",
        "subjects": "subjects", "provenance": "provenance", "notes": "notes",
        "contents": "contents", "claims": "the item's own statements",
        "descriptions": "the item's own descriptions",
        "authority_evidence": "VIAF/Mazal authority evidence",
        "none": "no evidence anywhere",
    },
}
_MATCH_KIND = {
    "exact_or_vowelized": "exact match or vowelization variant (same consonants)",
    "trimmed_or_extended": "trimmed or extended: a prefix/suffix/substring match",
    "absent": "not present in the MARC evidence",
    "different_entity": "present in MARC but refers to a different real-world entity",
}
_NAME_QUALITY = {
    "specific_and_substantive": (
        "label/description identify the entity specifically and carry substance"
    ),
    "system_label_ok": (
        "intentional system label carrying MS control number/period "
        "plus a substantive description"
    ),
    "generic_fallback": (
        "generic placeholder (e.g. '... in the Hebrew Manuscripts "
        "Ontology (HMO)') without substance"
    ),
    "malformed": (
        "malformed label (unbalanced quotes, trailing punctuation in "
        "quotes, 'und' language code)"
    ),
}


def _choice(qid: str, instructions: str, criteria: dict[str, str]) -> dict[str, Any]:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def questions_for(
    evaluator_id: str,
    candidate: Any = None,
    max_claims: int = 8,
) -> dict[str, dict[str, Any]]:
    qs: dict[str, dict[str, Any]] = {
        NAXIS: _choice(NAXIS, _NAME_OK, {"yes": "present as claimed",
                                         "partial": "trimmed/extended match",
                                         "no": "absent or different entity"}),
        TAXIS: _choice(TAXIS, _TYPE_OK, {"yes": "type is correct",
                                         "partial": "correct but truncated/ambiguous",
                                         "no": "clearly the wrong type"}),
    }
    if evaluator_id == "person_ner":
        qs[RAXIS] = _choice(RAXIS, _ROLE_OK_PERSON, {
            "yes": "role supported by the role-mapped MARC field(s)",
            "partial": "adjacent but weaker evidence for the role",
            "no": "MARC assigns a different role",
        })
    elif evaluator_id == "hmo_wikibase_item":
        qs[RAXIS] = _choice(
            RAXIS,
            "Applying the evaluation brief in the state, are the item's claims "
            "and structural checks acceptable? 'not_applicable' ONLY for "
            "structural entity types — CatalogStep, EvidenceStep, "
            "EvidenceChain, Evidence, PhilologicalView, CatalogingView, "
            "ViewType, paradigm individuals — with no SHACL blocking issues. "
            "For every other entity type judge the claims themselves: 'yes' = "
            "claims are plausible and consistent with the item's labels and "
            "the MARC context; 'partial' = claims are weak, thin, or carry "
            "minor attribution doubts (removable, curator decides); 'no' = a "
            "claim contradicts the item's labels/MARC context, or SHACL "
            "issues block approval.",
            {"yes": "claims plausible and consistent",
             "partial": "claims weak or thin",
             "no": "a wrong claim or SHACL blocking issues",
             "not_applicable": "structural item only"},
        )
        qs["name_quality"] = _choice(
            "name_quality",
            "Applying the evaluation brief in the state (especially the "
            "per-entity-type guidance), which best describes the "
            "label/description quality of this item?",
            _NAME_QUALITY,
        )
        qs["text_quality"] = {
            "type": "noul",
            "instructions": (
                "Read the item's label and description text character by "
                "character. Flag ANY generation artifact: unclosed parentheses "
                "or quotes, text cut off mid-word or mid-folio-range (e.g. "
                "'folios 1' with no end value), duplicated function words "
                "('for in'), a label that names a different manuscript than "
                "the description, 'und' language codes, or a template "
                "description that only repeats the label. 1.0 = at least one "
                "artifact present; 0.0 = the text is clean and complete."
            ),
        }
    else:  # wikidata_item — role_ok = statement/evidence acceptability
        qs[RAXIS] = _choice(
            RAXIS,
            "Applying the evaluation brief in the state, are the item's "
            "statements, qualifiers, references, and any listed validation "
            "issues acceptable for the supplied evidence pack (MARC + VIAF + "
            "Mazal + existing Wikidata + HMO Wikibase)? 'yes' = every present "
            "claim is supported by at least one evidence channel and no "
            "validator issue signals a real public-data problem; 'partial' = "
            "mostly correct but carries removable bad claims; 'no' = a "
            "present claim is unsupported by every channel, modeled in the "
            "wrong place, contaminated, OR an ERROR-severity validation issue "
            "is present (an error-severity issue is a real public-data "
            "problem — an identifierless person item is not publishable). "
            "Absent claims are never defects — judge what is present. "
            "Provenance is ESTABLISHED by the claim's own references: a "
            "claim carrying P248 (stated in) + P3959 (NNL catalog ID) / P854 "
            "URL is supported even when the MARC slice does not repeat the "
            "fact. A claim backed by the Mazal pack (e.g. P8189 with its "
            "Ktiv reference) is supported.",
            {"yes": "statements acceptable for the evidence",
             "partial": "mostly correct, removable bad claims present",
             "no": "unsupported claim, wrong place, or ERROR-severity issue"},
        )
        # Statement-level checks: one noul per claim in the item's statements
        # sample — the explicit counterpart of the tier-1 judge's claim-by-
        # claim rubric pass ("same statements, same rules").
        claims = (getattr(candidate, "payload", None) or {}).get("statements") or []
        for i, stmt in enumerate(claims[:max_claims]):
            if not isinstance(stmt, dict):
                continue
            prop = stmt.get("property") or "?"
            value = stmt.get("value_label") or stmt.get("value") or "?"
            prop_label = stmt.get("property_label") or ""
            qs[f"claim_{i}"] = {
                "type": "noul",
                "instructions": (
                    f"The item carries the statement `statements[{i}]`: "
                    f"{prop_label} ({prop}) = {value}. Is this statement "
                    "supported by at least one evidence channel in the state? "
                    "Channels: (a) the statement's own `references` (stated-in "
                    "item, external-id, or URL); (b) `work_candidate_evidence` "
                    "— an accepted/curator-approved work entry with a "
                    "source_field supports the work's P31/title/author "
                    "claims; (c) per-claim MARC provenance `claim_sources`; "
                    "(d) the MARC slice itself (title, authors, 500/505 "
                    "fields); (e) VIAF / Mazal authority packs; (f) existing "
                    "Wikidata or the HMO Wikibase pack. Property hints: P31 "
                    "written-work is supported by an accepted "
                    "work_candidate_evidence entry; P1476 title by a MARC "
                    "245/500 title or the accepted work title; P2093 "
                    "author-name string by the name in MARC authors/500; "
                    "P50 by authority evidence. P973 (described-at URL) is "
                    "supported when the catalog/access URL appears in the "
                    "MARC slice (856/966 digital access) or the claim's own "
                    "references. P569/P570 (birth/death dates) are supported "
                    "when the dates appear in the MARC slice (100$d, 008, "
                    "260$c, 264$c, notes) or an authority pack. "
                    "1.0 = at least one channel "
                    "supports it; 0.0 = no channel names this property or "
                    "its source."
                ),
            }
    fields = _EVIDENCE_FIELDS[evaluator_id]
    qs["evidence_field"] = _choice(
        "evidence_field",
        "Which evidence source in the state is the primary basis for your "
        f"'{NAXIS}' answer? Pick where the deciding text lives.",
        fields,
    )
    if evaluator_id not in ("genre_classifier", "hmo_wikibase_item", "wikidata_item"):
        qs["match_kind"] = _choice(
            "match_kind",
            "Applying the universal definitions in the state, what kind of "
            "textual match is the predicted span against the MARC evidence?",
            _MATCH_KIND,
        )
    return qs


# ── gold set tooling ──────────────────────────────────────────────────────
async def _silver_labels(
    channel: str, run_id: uuid.UUID, keys: list[str],
) -> dict[str, str]:
    """Pre-fill gold labels from persisted curator approvals (read-only)."""
    if not keys:
        return {}
    key_set = set(keys)
    out: dict[str, str] = {}
    async with session_scope() as db:
        if channel == "hmo":
            from app.models.hmo_studio_item_override import HmoStudioItemOverride

            rows = (await db.execute(
                select(HmoStudioItemOverride).where(
                    HmoStudioItemOverride.run_id == run_id,
                ),
            )).scalars().all()
            for r in rows:
                if r.local_id in key_set and r.approved is not None:
                    out[r.local_id] = "full" if r.approved else "fail"
        elif channel == "wikidata":
            from app.models.item_override import WikidataItemOverride

            rows = (await db.execute(
                select(WikidataItemOverride).where(
                    WikidataItemOverride.run_id == run_id,
                ),
            )).scalars().all()
            for r in rows:
                if r.local_id in key_set and r.approved is not None:
                    out[r.local_id] = "full" if r.approved else "fail"
        else:
            from app.models.extraction_approval import ExtractionApproval

            rows = (await db.execute(
                select(ExtractionApproval).where(
                    ExtractionApproval.run_id == run_id,
                ),
            )).scalars().all()
            for r in rows:
                rid = str(r.id)
                if rid in key_set and r.approved is not None:
                    out[rid] = "full" if r.approved else "fail"
    return out


async def _baseline_overall_map(
    channel: str, run_id: uuid.UUID, keys: list[str],
) -> dict[str, str]:
    """Persisted production ai_verdict overall per key (for balancing only)."""
    if not keys:
        return {}
    key_set = set(keys)
    out: dict[str, str] = {}
    async with session_scope() as db:
        if channel == "hmo":
            from app.models.hmo_studio_item_override import HmoStudioItemOverride

            rows = (await db.execute(
                select(HmoStudioItemOverride).where(
                    HmoStudioItemOverride.run_id == run_id,
                ),
            )).scalars().all()
            for r in rows:
                v = _baseline_verdict(r.ai_verdict)
                if r.local_id in key_set and v.get("overall"):
                    out[r.local_id] = str(v["overall"])
        elif channel == "wikidata":
            from app.models.item_override import WikidataItemOverride

            rows = (await db.execute(
                select(WikidataItemOverride).where(
                    WikidataItemOverride.run_id == run_id,
                ),
            )).scalars().all()
            for r in rows:
                v = _baseline_verdict(r.ai_verdict)
                if r.local_id in key_set and v.get("overall"):
                    out[r.local_id] = str(v["overall"])
        else:
            from app.models.extraction_approval import ExtractionApproval

            rows = (await db.execute(
                select(ExtractionApproval).where(
                    ExtractionApproval.run_id == run_id,
                ),
            )).scalars().all()
            for r in rows:
                v = _baseline_verdict(r.ai_verdict)
                if str(r.id) in key_set and v.get("overall"):
                    out[str(r.id)] = str(v["overall"])
    return out


def _balanced_scope(
    candidates: list[tuple[Any, Any]],
    baseline: dict[str, str],
    evaluator_id: str,
    total: int,
) -> list[tuple[Any, Any]]:
    """Stratify candidates across baseline overall classes, ~total/3 each."""
    classes: dict[str, list[Any]] = {"full": [], "partial": [], "fail": []}
    other: list[Any] = []
    for _rec, cand in candidates:
        b = baseline.get(_candidate_key(evaluator_id, dict(cand.payload)))
        bucket = classes.get(b)
        (bucket if bucket is not None else other).append((_rec, cand))
    per = max(1, total // 3)
    picked: list[tuple[Any, Any]] = []
    leftovers: list[tuple[Any, Any]] = []
    for rows in classes.values():
        picked.extend(rows[:per])
        leftovers.extend(rows[per:])
    pool = leftovers + other
    for row in pool:
        if len(picked) >= total:
            break
        picked.append(row)
    return picked


def _row_summary(
    cand: Any, evaluator_id: str, evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    p = dict(cand.payload)
    if evaluator_id in ("hmo_wikibase_item", "wikidata_item"):
        out: dict[str, Any] = {
            "entity_type": p.get("entity_type"),
            "labels": p.get("labels") or {},
            "descriptions": p.get("descriptions") or {},
            "statement_count": p.get("statement_count"),
        }
        if evidence:
            out["marc_context"] = evidence
        if evaluator_id == "wikidata_item":
            out["statements"] = (p.get("statements") or [])[:12]
            out["validation_issues"] = p.get("validation_issues") or []
            out["existing_qid"] = p.get("existing_qid")
            out["authority_evidence"] = p.get("authority_evidence") or []
            wce = p.get("work_candidate_evidence") or {}
            if isinstance(wce, list):
                out["work_candidate_evidence"] = wce[:4]
            elif isinstance(wce, dict):
                out["work_candidate_evidence"] = wce
        return out
    out = {
        "text": p.get("text") or p.get("person") or p.get("label") or "",
        "type": p.get("type"),
        "role": p.get("role"),
        "source": p.get("source"),
        "confidence": p.get("confidence"),
        "grounded": p.get("grounded"),
        "exists_in": (p.get("exists_in") or [])[:6],
    }
    if evidence:
        out["marc_context"] = evidence
    return out


def _export_gold_sheet(
    path: Path,
    channel: str,
    evaluator_id: str,
    run_id: uuid.UUID,
    candidates: list[tuple[Any, Any]],
    silver: dict[str, str],
    *,
    baseline: dict[str, str] | None = None,
    with_evidence: bool = False,
) -> int:
    rows = []
    for _rec, cand in candidates:
        payload = dict(cand.payload)
        key = _candidate_key(evaluator_id, payload)
        evidence = getattr(cand, "marc_context", None) if with_evidence else None
        row: dict[str, Any] = {
            "channel": channel,
            "evaluator": evaluator_id,
            "run_id": str(run_id),
            "key": key,
            "record_id": getattr(cand, "record_id", ""),
            "summary": _row_summary(cand, evaluator_id, evidence),
            "label": {
                "overall": silver.get(key, ""),
                "axes": {},
                "notes": "",
                "source": "silver" if key in silver else "",
            },
        }
        if baseline is not None:
            row["baseline_overall"] = baseline.get(key, "")
        rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
        encoding="utf-8",
    )
    silver_n = sum(1 for r in rows if r["label"]["overall"])
    print(f"[gold] wrote {len(rows)} row(s) to {path} "
          f"({silver_n} silver pre-filled, {len(rows) - silver_n} need labels)")
    print("[gold] fill label.overall with full|partial|fail "
          "(axes.name_ok/type_ok/role_ok optional), then pass the file "
          "back with --gold")
    return len(rows)


def _load_gold(path: Path) -> dict[str, dict[str, Any]]:
    gold: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        label = row.get("label") or {}
        if row.get("key") and str(label.get("overall") or ""):
            gold[str(row["key"])] = row
    return gold


def _score_vs_gold(
    judged: list[dict[str, Any]],
    gold: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    scored = [r for r in judged if r.get("key") in gold]
    matrix: Counter[str] = Counter()
    unsafe: list[str] = []
    safe_miss: list[str] = []
    agree = 0
    axis_pairs: dict[str, list[tuple[str, str]]] = {a: [] for a in (NAXIS, TAXIS, RAXIS)}
    for r in scored:
        g = (gold[r["key"]].get("label") or {})
        go = str(g.get("overall"))
        jo = str(r["overall"])
        matrix[f"{go}->{jo}"] += 1
        if go == jo:
            agree += 1
        if jo == "full" and go in ("partial", "fail"):
            unsafe.append(r["key"])
        elif jo == "fail" and go == "full":
            safe_miss.append(r["key"])
        gaxes = g.get("axes") or {}
        for axis in (NAXIS, TAXIS, RAXIS):
            gv = _norm_axis(gaxes.get(axis))
            jv = r["axes"].get(axis) or "unknown"
            if gv in ("yes", "partial", "no") and jv != "unknown":
                axis_pairs[axis].append((gv, jv))
    n = len(scored)
    axis_reports = {}
    for axis, pairs in axis_pairs.items():
        if pairs:
            ok = sum(1 for b, j in pairs if b == j)
            axis_reports[axis] = {"n": len(pairs), "agreement": round(ok / len(pairs), 3)}
    return {
        "gold_rows": len(gold),
        "scored": n,
        "unlabeled_skipped": len(judged) - n,
        "accuracy": round(agree / n, 3) if n else None,
        "unsafe_approve": {"count": len(unsafe), "keys": unsafe[:20]},
        "safe_miss": {"count": len(safe_miss), "keys": safe_miss[:20]},
        "confusion": dict(matrix),
        "axis_vs_gold": axis_reports,
    }


# ── verdict mapping ───────────────────────────────────────────────────────
def _norm_axis(value: Any) -> str:
    v = str(value or "").strip().lower()
    if v in ("not_applicable", "n/a", "na"):
        return "n/a"
    return v if v in ("yes", "partial", "no") else "unknown"


def synthesize_reasoning(
    axes: dict[str, str],
    evidence_field: str,
    match_kind: str,
    answers: dict[str, Any],
) -> str:
    parts = [
        f"{axis}={value}" for axis, value in axes.items() if value != "unknown"
    ]
    if match_kind and match_kind != "unknown":
        parts.append(f"match={match_kind}")
    if evidence_field and evidence_field != "none":
        parts.append(f"evidence in {evidence_field}")
    nq = answers.get("name_quality")
    if isinstance(nq, dict) and nq.get("choice"):
        parts.append(f"label quality: {nq['choice']}")
    tq = answers.get("text_quality")
    if isinstance(tq, dict) and isinstance(tq.get("noul"), (int, float)) \
            and tq["noul"] >= 0.5:
        parts.append(f"text artifacts flagged (noul={tq['noul']:.2f})")
    conf = answers.get(NAXIS, {}).get("confidence")
    if isinstance(conf, (int, float)):
        parts.append(f"p={conf:.2f}")
    return "; ".join(parts) + "."


# ── Jev client ────────────────────────────────────────────────────────────
class JevError(RuntimeError):
    pass


async def jev_request(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    model: str,
    state: str,
    questions: dict[str, dict[str, Any]],
    timeout_cfg: float,
    max_retries: int = 3,
) -> tuple[dict[str, Any], float, dict[str, int] | None]:
    payload = {"state": state, "model": model, "questions": questions}
    headers = {"Authorization": f"Bearer {api_key}"}
    start = time.monotonic()
    delay = 2.0
    last_err = ""
    for _attempt in range(max_retries + 1):
        try:
            resp = await client.post(
                TYPESAFE_URL, json=payload, headers=headers, timeout=timeout_cfg,
            )
            if resp.status_code in (429,) or resp.status_code >= 500:
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                retry_after = resp.headers.get("retry-after")
                wait = float(retry_after) if retry_after else delay
                await asyncio.sleep(min(wait, 60.0))
                delay *= 2
                continue
            if resp.status_code >= 400:
                # A 4xx is a per-row problem (e.g. oversized request) — fail
                # that row, never the batch.
                raise JevError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            latency = time.monotonic() - start
            body = resp.json()
            usage = body.get("usage") or None
            return body.get("answers") or {}, latency, usage
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(delay)
            delay *= 2
    raise JevError(
        f"TypeSafe request failed after {max_retries + 1} attempts: {last_err}"
    )


# ── report helpers ────────────────────────────────────────────────────────
def _baseline_verdict(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not raw:
        return {}
    inner = raw.get("verdict")
    if isinstance(inner, dict) and inner.get("overall"):
        return inner
    return raw


def _latency_summary(latencies: list[float]) -> dict[str, float]:
    if not latencies:
        return {}
    s = sorted(latencies)
    return {
        "mean_s": round(statistics.mean(s), 2),
        "p50_s": round(s[len(s) // 2], 2),
        "p95_s": round(s[min(len(s) - 1, int(len(s) * 0.95))], 2),
    }


def _axis_agreement(
    pairs: list[tuple[dict[str, Any], dict[str, str]]], axis: str,
) -> dict[str, Any] | None:
    total = agree = 0
    dist: Counter[str] = Counter()
    for base, jev in pairs:
        b = _norm_axis(base.get(axis))
        j = jev.get(axis) or "unknown"
        if b in ("unknown", "") or j == "unknown":
            continue
        total += 1
        dist[f"{b}|{j}"] += 1
        if b == j:
            agree += 1
    if not total:
        return None
    return {"n": total, "agreement": round(agree / total, 3), "confusion": dict(dist)}


# ── check list (RuleResult contract) ─────────────────────────────────────
# Mirrors backend/app/pipeline/rule_verify/base.py: one check per atomic
# judgment, explicit state, human message, evidence. `partial` is the Jev
# extension the rules engine does not need (graded judgment, not boolean).
_AXIS_TO_STATE = {
    "yes": "pass", "partial": "partial", "no": "fail", "n/a": "not_applicable",
}
_AXIS_LABELS = {
    NAXIS: "Identity grounded in evidence",
    TAXIS: "Entity type correct",
    RAXIS: "Role / claims acceptable",
}
_MATCH_STATE = {
    "exact_or_vowelized": "pass",
    "trimmed_or_extended": "partial",
    "absent": "fail",
    "different_entity": "fail",
}
_NAME_QUALITY_STATE = {
    "specific_and_substantive": "pass",
    "system_label_ok": "pass",
    "generic_fallback": "partial",
    "malformed": "fail",
}


def _check(
    rule_id: str,
    label: str,
    state: str,
    message: str,
    *,
    field: str = "",
    evidence: dict[str, Any] | None = None,
    blocking: bool = True,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "label": label,
        "state": state,
        "field": field,
        "message": message,
        "evidence": evidence or {},
        "blocking": blocking,
    }


def _axis_check(
    axis: str,
    raw: str,
    final: str,
    confidence: Any,
    field: str,
) -> dict[str, Any]:
    state = _AXIS_TO_STATE.get(final, "error")
    if state == "not_applicable":
        message = "not applicable for this entity type"
    elif state == "pass":
        message = f"{_AXIS_LABELS[axis]} — confirmed"
    elif state == "partial":
        if final != raw:
            message = (
                f"{_AXIS_LABELS[axis]} — raw judgment '{raw}' at confidence "
                f"{float(confidence):.2f}; low confidence routes to review"
            )
        else:
            message = f"{_AXIS_LABELS[axis]} — partially supported"
    else:
        message = f"{_AXIS_LABELS[axis]} — not supported by the evidence"
    ev: dict[str, Any] = {"raw": raw}
    if isinstance(confidence, (int, float)):
        ev["confidence"] = round(float(confidence), 3)
    return _check(
        f"jev.{axis}", _AXIS_LABELS[axis], state, message,
        field=field, evidence=ev,
    )


def _build_checks(
    evaluator_id: str,
    raw_axes: dict[str, str],
    final: dict[str, str],
    confidences: dict[str, Any],
    evidence_field: str,
    match_kind: str,
    artifact_noul: Any,
    name_quality: str,
    notes: list[str],
    claims: list[dict[str, Any]] | None = None,
    answers: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    checks = [
        _axis_check(axis, raw_axes[axis], final[axis], confidences.get(axis),
                    evidence_field if axis == NAXIS else "")
        for axis in (NAXIS, TAXIS, RAXIS)
    ]
    if evaluator_id == "hmo_wikibase_item" and name_quality:
        state = _NAME_QUALITY_STATE.get(name_quality, "error")
        checks.append(_check(
            "jev.name_quality", "Label / description quality", state,
            f"label quality: {name_quality.replace('_', ' ')}",
            field=evidence_field,
            evidence={"raw": name_quality},
            blocking=state == "fail",
        ))
    if evaluator_id == "hmo_wikibase_item" and \
            isinstance(artifact_noul, (int, float)):
        checks.append(_check(
            "jev.text_quality", "No generation artifacts",
            "partial" if artifact_noul >= 0.5 else "pass",
            (
                f"artifacts flagged in label/description text (noul="
                f"{artifact_noul:.2f})"
                if artifact_noul >= 0.5
                else f"label/description text clean (noul={artifact_noul:.2f})"
            ),
            field=evidence_field,
            evidence={"noul": round(float(artifact_noul), 3)},
            blocking=False,
        ))
    if evaluator_id not in ("genre_classifier", "hmo_wikibase_item",
                            "wikidata_item") and match_kind:
        checks.append(_check(
            "jev.match_kind", "Textual match kind",
            _MATCH_STATE.get(match_kind, "error"),
            f"match kind: {match_kind.replace('_', ' ')}",
            field=evidence_field,
            evidence={"raw": match_kind},
            blocking=False,
        ))
    # Statement-level checks (wikidata_item): one per claim in the sampled
    # statements. An unsupported claim is a removable finding — it caps the
    # item at partial (rubric: "mostly correct items with removable bad
    # claims"), so it is non-blocking for the overall verdict.
    if evaluator_id == "wikidata_item" and claims and answers:
        for i, stmt in enumerate(claims):
            ans = answers.get(f"claim_{i}")
            if not isinstance(ans, dict):
                continue
            noul = ans.get("noul")
            if not isinstance(noul, (int, float)):
                continue
            prop = stmt.get("property") or "?"
            value = str(stmt.get("value_label") or stmt.get("value") or "?")
            if noul >= 0.7:
                state, msg = "pass", "claim supported by the evidence pack"
            elif noul >= 0.4:
                state, msg = "partial", (
                    f"claim support uncertain (noul={noul:.2f}) — review"
                )
            else:
                state, msg = "fail", (
                    f"claim unsupported by every evidence channel "
                    f"(noul={noul:.2f}) — remove or re-source before upload"
                )
            checks.append(_check(
                f"jev.claim.{prop}",
                f"Statement {prop} = {value}",
                state, msg,
                field="statements",
                evidence={"noul": round(float(noul), 3)},
                blocking=False,
            ))
    return checks


def _overall_from_checks(checks: list[dict[str, Any]]) -> str:
    states = [c["state"] for c in checks]
    if any(c["state"] == "fail" and c.get("blocking", True) for c in checks):
        return "fail"
    if "fail" in states or "partial" in states:
        return "partial"
    return "full"


_CHECK_CHIP = {
    "pass": ("#0f766e", "#ecfdf5", "#a7f3d0"),
    "partial": ("#92400e", "#fffbeb", "#fde68a"),
    "fail": ("#b91c1c", "#fef2f2", "#fecaca"),
    "not_applicable": ("#52525b", "#fafafa", "#e4e4e7"),
    "error": ("#9a3412", "#fff7ed", "#fed7aa"),
}
_OVERALL_CHIP = {
    "full": _CHECK_CHIP["pass"],
    "partial": _CHECK_CHIP["partial"],
    "fail": _CHECK_CHIP["fail"],
}


def _chip(state: str, text: str) -> str:
    fg, bg, border = _CHECK_CHIP.get(state, _CHECK_CHIP["error"])
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:6px;'
        f'font-size:11px;font-weight:600;color:{fg};background:{bg};'
        f'border:1px solid {border};white-space:nowrap">{_html.escape(text)}</span>'
    )


def _overall_chip(state: Any, text: str | None = None) -> str:
    s = str(state or "")
    if s == "pass":  # legacy synonym of full in older caches
        s = "full"
    return _chip(s if s in _OVERALL_CHIP else "fail", text or s or "?")


def _write_checks_html(path: Path, report: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Render the bake-off as a rules-panel style report: per-check summary
    plus one card per entity listing every check and its state."""
    esc = _html.escape
    rule_labels: dict[str, str] = {}
    for r in rows:
        for c in r["checks"]:
            rule_labels.setdefault(c["rule_id"], c.get("label") or c["rule_id"])
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<title>Jev bake-off checks</title><style>",
        "body{font-family:-apple-system,'Segoe UI',sans-serif;margin:24px;"
        "color:#18181b;max-width:1100px}",
        "h1{font-size:20px} h2{font-size:15px;margin-top:28px}",
        "table{border-collapse:collapse;width:100%;font-size:13px}",
        "th,td{border:1px solid #e4e4e7;padding:6px 10px;text-align:left}",
        "th{background:#fafafa;font-size:12px;text-transform:uppercase;"
        "letter-spacing:.02em}",
        ".card{border:1px solid #e4e4e7;border-radius:10px;padding:12px 16px;"
        "margin:12px 0;background:#fff}",
        ".card h3{margin:0 0 6px;font-size:14px;font-weight:600;"
        "font-family:ui-monospace,monospace;word-break:break-all}",
        ".meta{color:#71717a;font-size:12px;margin:2px 0 8px}",
        ".check{display:flex;gap:8px;align-items:baseline;padding:3px 0;"
        "font-size:13px}",
        ".check .msg{color:#3f3f46}",
        ".check .conf{color:#a1a1aa;font-size:11px}",
        ".muted{color:#71717a}",
        "</style></head><body>",
        "<h1>Jev bake-off — per-check results</h1>",
        f"<p class='muted'>{esc(str(report['evaluator']))} · "
        f"run {esc(str(report['run_id'])[:8])} · Jev "
        f"{esc(str(report['jev_model']))}"
        + (
            f" vs tier-1 {esc(str(report['tier1_model']))}"
            if report.get("tier1_model") else " (gold scoring mode)"
        )
        + f" · overall agreement {report['overall_agreement']} · "
        f"{report['judged']} judged, {report['errors']} errors</p>",
    ]
    if report.get("gold"):
        g = report["gold"]
        parts.append(
            "<div class='card'><h3>Gold-set scoring</h3>"
            f"<p class='meta'>scored {g['scored']} of {g['gold_rows']} gold rows "
            f"({g['unlabeled_skipped']} unlabeled skipped) · "
            f"accuracy {g['accuracy']} · "
            f"<b>UNSAFE-approve {g['unsafe_approve']['count']}</b> · "
            f"safe-miss {g['safe_miss']['count']}</p>"
            + "".join(
                f"<div class='check'>"
                f"{_chip('fail' if k.split('->')[0] == 'fail' and v else 'pass', k)}"
                f"<span class='msg'>{v} row(s)</span></div>"
                for k, v in sorted(g["confusion"].items())
            )
            + (f"<p class='meta'>unsafe keys: {esc(', '.join(g['unsafe_approve']['keys']))}</p>"
               if g["unsafe_approve"]["keys"] else "")
            + "</div>"
        )
    if report.get("determinism"):
        d = report["determinism"]
        parts.append(
            "<div class='card'><h3>Determinism (double run)</h3>"
            f"<p class='meta'>n={d['n']} · overall stability {d['overall_stability']} · "
            f"axis stability {d['axis_stability']}</p>"
            + "".join(
                f"<div class='check'>{_chip('partial', 'changed')}"
                f"<span class='msg'>{esc(str(c['key'])[:60])} "
                f"{esc(str(c['run1']))}→{esc(str(c['run2']))}</span></div>"
                for c in d["changed"][:10]
            )
            + "</div>"
        )
    parts.append("<h2>Check summary</h2>")
    parts.append(
        "<table><tr><th>Check</th><th>Label</th><th>pass</th><th>partial</th>"
        "<th>fail</th><th>n/a</th></tr>",
    )
    for rid, tally in sorted(report.get("check_summary", {}).items()):
        parts.append(
            f"<tr><td><code>{esc(rid)}</code></td>"
            f"<td>{esc(rule_labels.get(rid, ''))}</td>"
            f"<td>{tally.get('pass', 0)}</td>"
            f"<td>{tally.get('partial', 0)}</td>"
            f"<td>{tally.get('fail', 0)}</td>"
            f"<td>{tally.get('not_applicable', 0)}</td></tr>"
        )
    parts.append("</table><h2>Entities</h2>")
    for r in rows:
        etype = str((r.get("candidate") or {}).get("entity_type")
                    or r.get("sub_type") or "")
        base_overall = str(r["baseline"].get("overall") or "?")
        parts.append("<div class='card'>")
        parts.append(
            f"<h3>{esc(str(r.get('key') or r.get('record_id') or '?'))} "
            f"{_overall_chip(r['overall'])} "
            f"{_overall_chip(base_overall, f'tier-1: {base_overall}')}</h3>"
        )
        parts.append(
            f"<p class='meta'>{esc(etype)} · latency "
            f"{r.get('latency_s')}s · evidence: "
            f"{esc(str(r.get('evidence_field')))} · "
            f"{esc(str(r.get('reasoning') or ''))}</p>"
        )
        for c in r["checks"]:
            conf = (c.get("evidence") or {}).get("confidence")
            conf_s = f" · conf {conf:.2f}" if isinstance(conf, (int, float)) else ""
            field = f" · {esc(str(c.get('field')))}" if c.get("field") else ""
            parts.append(
                f"<div class='check'>{_chip(c['state'], c['state'])}"
                f"<b>{esc(str(c.get('label') or c['rule_id']))}</b>"
                f"<span class='msg'>{esc(str(c.get('message') or ''))}"
                f"{field}</span><span class='conf'>{conf_s}</span></div>"
            )
        parts.append("</div>")
    parts.append(f"<p class='muted'>checks referenced: "
                 f"{len(rule_labels)} · generated "
                 f"{datetime.now(UTC).isoformat()}</p>")
    parts.append("</body></html>")
    path.write_text("\n".join(parts), encoding="utf-8")


def _deterministic_text_artifacts(
    payload: dict[str, Any], marc_context: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Mechanical label/description artifact checks (code, not judgment).

    Mirrors what the gold curator flags deterministically: unbalanced
    parentheses per text, item-CN vs description-CN mismatches, truncated
    folio ranges, and quote-collision garbles (spaces lost at a Hebrew
    gershayim boundary vs MARC 245).
    """
    labels = payload.get("labels") or {}
    descs = payload.get("descriptions") or {}
    findings: list[tuple[str, str]] = []
    texts = [str(v) for v in list(labels.values()) + list(descs.values()) if v]
    for t in texts:
        if t.count("(") != t.count(")"):
            findings.append((
                "unclosed parenthesis",
                f"text has {t.count('(')} open vs {t.count(')')} close "
                f"parentheses: {t[:60]}",
            ))
            break
    import re as _re
    cn = _re.compile(r"990\d{12,15}")
    # Identity CN = the one embedded in the item's own local id (W-137), not
    # the propagated linked set (W-48).
    identity_cns = set(
        cn.findall(str(payload.get("_local_id") or ""))
    ) or set(cn.findall(str(payload.get("source_uri") or "")))
    if not identity_cns:
        cns = [str(x) for x in payload.get("control_numbers") or []]
        identity_cns = {cns[0]} if cns else set()
    desc_cns = set(cn.findall(" ".join(str(v) for v in descs.values())))
    if identity_cns and desc_cns and not (desc_cns & identity_cns):
        findings.append((
            "manuscript mismatch",
            f"item identity CN {sorted(identity_cns)[0]} but description "
            f"cites {sorted(desc_cns)[0]}",
        ))
    if any(_re.search(r"folios? \d+[\s.]*$", t) for t in texts):
        findings.append((
            "truncated folio range",
            "a description ends on a folio number with no end value",
        ))
    if marc_context:
        title = str(marc_context.get("title") or "")

        def _loose(s: str) -> str:
            return _re.sub(r"[\s\"'״׳]+", "", s)

        if title:
            for v in (str(x) for x in list(labels.values()) + list(descs.values()) if x):
                # Garble = two abbreviations merged across a gershayim
                # boundary (e.g. שד"ר + כה"ר → שד"רכה"ר): a quote followed
                # by 2+ Hebrew letters. Legitimate abbreviations (שד"ר,
                # יצ"ו) keep exactly one letter after the quote.
                if len(v) >= 8 and _re.search(r"[א-ת]\"[א-ת]{2,}", v) \
                        and _loose(v) in _loose(title) and v not in title:
                    findings.append((
                        "quote-collision garble",
                        "label/description matches MARC 245 only with "
                        f"spaces/quotes collapsed: {v[:50]}",
                    ))
                    break
    return findings


def _map_row(
    evaluator_id: str,
    cand: Any,
    base: dict[str, Any],
    answers: dict[str, Any],
    latency: float,
    usage: dict[str, int] | None,
) -> dict[str, Any]:
    raw_axes = {
        NAXIS: _norm_axis((answers.get(NAXIS) or {}).get("choice")),
        TAXIS: _norm_axis((answers.get(TAXIS) or {}).get("choice")),
        RAXIS: (
            _norm_axis((answers.get(RAXIS) or {}).get("choice"))
            if RAXIS in answers else "n/a"
        ),
    }
    evidence = (answers.get("evidence_field") or {}).get("choice") or "unknown"
    match_kind = (answers.get("match_kind") or {}).get("choice") or ""
    confidences = {
        q: a.get("confidence") if isinstance(a.get("confidence"), (int, float))
        else a.get("noul")
        for q, a in answers.items()
        if isinstance(a, dict) and isinstance(
            a.get("confidence", a.get("noul")), (int, float),
        )
    }
    tq = answers.get("text_quality") or {}
    artifact_noul = tq.get("noul") if isinstance(tq, dict) else None
    artifact = isinstance(artifact_noul, (int, float)) and artifact_noul >= 0.5
    name_quality = (answers.get("name_quality") or {}).get("choice") or ""
    # Deterministic upload-gate check (wikidata_item): an ERROR-severity
    # validation issue means the item is not publishable — this is a rule,
    # not a judgment, so the confidence gate must never soften it.
    validator_error = False
    if evaluator_id == "wikidata_item":
        for issue in dict(cand.payload).get("validation_issues") or []:
            if isinstance(issue, dict) and \
                    str(issue.get("severity") or "").lower() == "error":
                validator_error = True
                break

    # Final verdict axes: rubric rule 5 (artifacts downgrade an accepted
    # label) + the confidence gate (an unsure blocking 'no' routes to
    # curator review, never to a hard fail) + the deterministic validator
    # gate (ERROR-severity issues always fail, never gated).
    final = dict(raw_axes)
    if artifact and final[NAXIS] == "yes":
        final[NAXIS] = "partial"
    notes: list[str] = []
    if validator_error and final[RAXIS] != "no":
        final[RAXIS] = "no"
        notes.append("role_ok: ERROR-severity validation issue — upload gate")
    artifacts_code = (
        _deterministic_text_artifacts(dict(cand.payload), cand.marc_context)
        if evaluator_id == "hmo_wikibase_item" else []
    )
    if artifacts_code and final[NAXIS] == "yes":
        final[NAXIS] = "partial"
        notes.append(
            "name_ok: deterministic text artifacts ("
            + "; ".join(name for name, _ in artifacts_code) + ")",
        )
    for axis in (NAXIS, TAXIS, RAXIS):
        if axis == RAXIS and validator_error:
            continue
        conf = confidences.get(axis)
        if final[axis] == "no" and isinstance(conf, (int, float)) \
                and conf < ROLE_CONF_GATE:
            final[axis] = "partial"
            notes.append(f"{axis}: 'no' at confidence {conf:.2f} — routed to review")

    checks = _build_checks(
        evaluator_id, raw_axes, final, confidences,
        evidence, match_kind, artifact_noul, name_quality, notes,
        claims=dict(cand.payload).get("statements") or [],
        answers=answers,
    )
    for name, msg in artifacts_code:
        checks.append(_check(
            f"jev.artifact.{name.split()[0]}", "Label/description artifacts",
            "partial", f"{name}: {msg}",
            field="labels/descriptions",
            evidence={"deterministic": True},
            blocking=False,
        ))
    overall = _overall_from_checks(checks)
    gate_note = (" Confidence gate: " + "; ".join(notes) + ".") if notes else ""
    return {
        "key": _candidate_key(evaluator_id, dict(cand.payload)),
        "evaluator_id": evaluator_id,
        "record_id": getattr(cand, "record_id", ""),
        "sub_type": getattr(cand, "sub_type", ""),
        "candidate": dict(cand.payload),
        "axes": raw_axes,
        "checks": checks,
        "overall": overall,
        "evidence_field": evidence,
        "match_kind": match_kind,
        "confidences": confidences,
        "reasoning": (
            synthesize_reasoning(final, evidence, match_kind, answers) + gate_note
        ),
        "baseline": base,
        "latency_s": round(latency, 2),
        "usage": usage or {},
    }


# ── main ──────────────────────────────────────────────────────────────────
async def _async_main(args: argparse.Namespace) -> int:
    _load_dotenv()
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL is required (Heroku prod or local .env).")
    api_key = _resolve_typesafe_key()
    if not api_key:
        raise SystemExit("missing TYPESAFE_API_KEY (env or heroku config:get)")
    _ensure_eval_agent_on_path()

    from eval_agent.evaluators import build as build_evaluator

    run_id = uuid.UUID(args.run_id)
    out_dir = (
        Path(args.output_dir).expanduser().resolve()
        / f"{args.channel}-{args.evaluator}-{run_id.hex[:8]}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. build the fixture (read-only)
    if args.channel == "ner":
        items, marc_records = await _build_ner(
            run_id, args.evaluator, max_predictions=args.limit,
        )
    elif args.channel == "hmo":
        items, marc_records = await _build_hmo(run_id, out_dir / "build")
        if not args.include_blank_nodes:
            exportable = [
                i for i in items
                if not str(i.get("local_id") or "").startswith("QDraft_BlankNode")
            ]
            print(f"[build] skipped {len(items) - len(exportable)} "
                  "non-exportable blank-node draft items")
            items = exportable
    else:
        items, marc_records = await _build_wikidata(run_id)
    scoped = items[: args.limit] if args.limit else items
    if not scoped:
        print("[scope] no items built — nothing to verify.")
        return 0
    print(f"[scope] {len(scoped)} item(s) go to BOTH judges "
          f"(tier-1: {args.tier_model}, jev: {args.model})")

    # 2. fixture files; tier-1 baseline and Jev run concurrently
    pipeline_dir = out_dir / "pipeline-output"
    _write_fixture(pipeline_dir, args.channel, scoped, marc_records)

    # 3. candidates from the SAME fixture files for both judges
    fixture_records = _load_fixture_records(args.channel, pipeline_dir)
    evaluator = build_evaluator(args.evaluator)
    candidates = _pairing_and_candidates(evaluator, fixture_records, marc_records)
    balance_export = args.export_gold and args.balance_total
    if args.limit and not balance_export:
        candidates = candidates[: args.limit]
    gold = _load_gold(Path(args.gold).expanduser()) if args.gold else {}
    if args.gold:
        print(f"[gold] scoring against {args.gold}: {len(gold)} labeled row(s)")
        if args.only_gold_keys:
            before = len(candidates)
            candidates = [
                (rec, cand) for rec, cand in candidates
                if _candidate_key(evaluator.id, dict(cand.payload)) in gold
            ]
            print(f"[gold] only-gold-keys: {len(candidates)} of {before} candidate(s)")
    print(f"[scope] {len(candidates)} candidate(s) to BOTH judges "
          f"(tier-1: {args.tier_model}, jev: {args.model})")

    states: list[str] = []
    keys: list[str] = []
    questions_list: list[dict[str, dict[str, Any]]] = []
    for _rec, cand in candidates:
        prompt = evaluator.build_prompt(cand)
        states.append(prompt.replace(
            "\nReturn only the JSON verdict.", f"\n{_JEV_CLOSE}",
        ))
        keys.append(_candidate_key(evaluator.id, dict(cand.payload)))
        questions_list.append(
            questions_for(args.evaluator, cand, args.max_claims),
        )
    claim_q = sum(1 for q in questions_list if any(k.startswith("claim_") for k in q))
    if claim_q:
        print(f"[jev] per-claim statement checks on {claim_q} item(s) "
              f"(≤{args.max_claims} claims each)")

    # Gold labeling sheet: no API keys needed — build, list, exit.
    if args.export_gold:
        silver = (
            await _silver_labels(args.channel, run_id, keys)
            if args.silver else {}
        )
        baseline: dict[str, str] = {}
        if args.balance_total:
            baseline = await _baseline_overall_map(args.channel, run_id, keys)
            dist = Counter(baseline.get(k, "none") for k in keys)
            print(f"[gold] baseline overall distribution: {dict(dist)}")
            candidates = _balanced_scope(
                candidates, baseline, evaluator.id, args.balance_total,
            )
            print(f"[gold] balanced scope: {len(candidates)} row(s) "
                  f"(target {args.balance_total})")
        _export_gold_sheet(
            Path(args.export_gold).expanduser(),
            args.channel, evaluator.id, run_id, candidates, silver,
            baseline=baseline or None,
            with_evidence=args.with_evidence,
        )
        return 0

    api_key = _resolve_typesafe_key()
    if not api_key:
        raise SystemExit("missing TYPESAFE_API_KEY (env or heroku config:get)")
    if not args.gold and not args.tier1_reuse:
        from scripts.local_measure_verify import _resolve_tier_key

        env_name, tier_key = _resolve_tier_key(args.tier_model)
        if not tier_key:
            raise SystemExit(f"missing {env_name} for tier model {args.tier_model}")

    sem = asyncio.Semaphore(args.concurrency)
    timeout_cfg = httpx.Timeout(args.timeout)
    timing: dict[str, float] = {}

    async def jev_pass(tag: str) -> list[dict[str, Any] | None]:
        rows_out: list[dict[str, Any] | None] = [None] * len(candidates)

        async def one(i: int) -> None:
            cand = candidates[i][1]
            async with sem:
                try:
                    answers, latency, usage = await jev_request(
                        client,
                        api_key=api_key, model=args.model,
                        state=states[i], questions=questions_list[i],
                        timeout_cfg=timeout_cfg,
                    )
                except JevError as exc:
                    rows_out[i] = {
                        "key": keys[i],
                        "error": str(exc), "latency_s": None,
                    }
                    return
            rows_out[i] = _map_row(evaluator.id, cand, {}, answers, latency, usage)

        t = time.monotonic()
        await asyncio.gather(*(one(i) for i in range(len(candidates))))
        timing[f"jev_s{tag}"] = round(time.monotonic() - t, 1)
        return rows_out

    async def run_tier1() -> tuple[dict[str, dict[str, Any]], int]:
        if args.gold:
            return {}, 0
        if args.tier1_reuse:
            return _load_tier1_results(out_dir / "eval-state")
        t = time.monotonic()
        out = await asyncio.to_thread(
            _run_tier1_baseline,
            pipeline_dir=pipeline_dir,
            state_dir=out_dir / "eval-state",
            evaluator=args.evaluator,
            tier_model=args.tier_model,
            rpm=args.rpm,
        )
        timing["tier1_s"] = round(time.monotonic() - t, 1)
        return out

    async with httpx.AsyncClient() as client:
        t0 = time.monotonic()
        coros: list[Any] = [jev_pass(""), run_tier1()]
        if args.determinism:
            coros.append(jev_pass("_det"))
        results = await asyncio.gather(*coros)
        wall = time.monotonic() - t0
    jev_rows = results[0]
    tier1_rows, tier1_tokens = results[1]
    det_rows = results[2] if args.determinism else None
    if args.gold:
        print(f"[jev] {len(jev_rows)} verdict(s) (gold mode — tier-1 skipped)")
    else:
        print(f"[tier-1] {len(tier1_rows)} verdict(s) from {args.tier_model}")

    # 4. merge baseline into rows + comparison + report
    rows = [r for r in jev_rows if r]
    for r in rows:
        r["baseline"] = _baseline_verdict(tier1_rows.get(r["key"]) or {})
        if gold and r.get("key") in gold:
            gl = gold[r["key"]].get("label") or {}
            r["baseline"] = {
                "overall": str(gl.get("overall") or ""),
                "reasoning": str(gl.get("notes") or ""),
                "judge_id": "gold",
            }
    errors = [r for r in rows if r.get("error")]
    judged = [r for r in rows if r and not r.get("error")]
    pairs = [(r["baseline"], r["axes"]) for r in judged]
    axis_reports = {
        axis: _axis_agreement(pairs, axis) for axis in (NAXIS, TAXIS, RAXIS)
    }
    matrix: Counter[str] = Counter()
    overall_agree = 0
    check_summary: dict[str, Counter[str]] = {}
    agree_rows = [r for r in judged if not gold or r.get("key") in gold]
    for r in agree_rows:
        b = str(r["baseline"].get("overall") or "unknown")
        j = r["overall"]
        matrix[f"{b}->{j}"] += 1
        if b == j:
            overall_agree += 1
        for c in r["checks"]:
            check_summary.setdefault(c["rule_id"], Counter())[c["state"]] += 1
    in_tokens = sum(r.get("usage", {}).get("input_tokens") or 0 for r in judged)
    latencies = [r["latency_s"] for r in judged if r.get("latency_s")]
    disagreements = [
        r for r in agree_rows if str(r["baseline"].get("overall")) != r["overall"]
    ][: args.sample]

    gold_report = _score_vs_gold(judged, gold) if gold else None
    determinism_report = None
    if det_rows is not None:
        det2 = {
            r["key"]: r for r in det_rows if r and not r.get("error")
        }
        n = 0
        same_overall = 0
        same_axes = 0
        changed = []
        for r in judged:
            r2 = det2.get(r["key"])
            if not r2:
                continue
            n += 1
            same_overall += r2["overall"] == r["overall"]
            same_axes += r2["axes"] == r["axes"]
            if r2["overall"] != r["overall"]:
                changed.append({
                    "key": r["key"],
                    "run1": r["overall"], "run2": r2["overall"],
                })
        determinism_report = {
            "n": n,
            "overall_stability": round(same_overall / n, 3) if n else None,
            "axis_stability": round(same_axes / n, 3) if n else None,
            "changed": changed[:20],
        }

    report = {
        "phase": "0-bakeoff",
        "channel": args.channel,
        "evaluator": args.evaluator,
        "run_id": str(run_id),
        "jev_model": args.model,
        "tier1_model": None if args.gold else args.tier_model,
        "fixture_size": len(scoped),
        "scope_size": len(candidates),
        "judged": len(judged),
        "errors": len(errors),
        "overall_agreement": round(overall_agree / len(agree_rows), 3) if agree_rows else None,
        "overall_matrix": dict(matrix),
        "axis_agreement": axis_reports,
        "check_summary": {k: dict(v) for k, v in check_summary.items()},
        "gold": gold_report,
        "determinism": determinism_report,
        "latency": _latency_summary(latencies),
        "wall_s": round(wall, 1),
        "timing": timing,
        "jev_input_tokens": in_tokens,
        "jev_est_cost_usd": round(in_tokens / 1_000_000 * PRICE_PER_MTOK, 4),
        "tier1_input_tokens": tier1_tokens,
        "disagreement_sample": disagreements,
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = out_dir / f"bakeoff_report_{stamp}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    rows_path = out_dir / f"bakeoff_rows_{stamp}.jsonl"
    rows_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows if r),
        encoding="utf-8",
    )

    print("\n===== BAKE-OFF (Jev vs tier-1, same fixture) =====")
    print(f"judged {len(judged)}/{len(candidates)}  errors {len(errors)}  "
          f"wall {report['wall_s']}s")
    if gold_report:
        g = gold_report
        print(f"GOLD: scored {g['scored']}  accuracy={g['accuracy']}  "
              f"UNSAFE-approve={g['unsafe_approve']['count']}  "
              f"safe-miss={g['safe_miss']['count']}")
        for k, v in sorted(g["confusion"].items()):
            print(f"  gold {k}: {v}")
        if g["unsafe_approve"]["keys"]:
            print(f"  unsafe keys: {g['unsafe_approve']['keys']}")
    if determinism_report:
        d = determinism_report
        print(f"DETERMINISM: n={d['n']}  overall stability={d['overall_stability']}  "
              f"axis stability={d['axis_stability']}")
        for c in d["changed"][:5]:
            print(f"  changed: {c['key'][:50]} {c['run1']}→{c['run2']}")
    print(f"overall agreement: {report['overall_agreement']}")
    for axis, ar in axis_reports.items():
        if ar:
            print(f"{axis}: n={ar['n']} agreement={ar['agreement']}")
    print(f"latency: {report['latency']}")
    print(f"jev input tokens: {in_tokens}  est cost: ${report['jev_est_cost_usd']}")
    print("check summary (pass/partial/fail per check):")
    for rid, tally in sorted(check_summary.items()):
        print(f"  {rid}: pass={tally.get('pass', 0)} "
              f"partial={tally.get('partial', 0)} fail={tally.get('fail', 0)} "
              f"n/a={tally.get('not_applicable', 0)}")
    print("transitions:")
    for k, v in sorted(matrix.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    print(f"\nwrote {report_path}")
    print(f"wrote {rows_path}")
    html_path = out_dir / f"bakeoff_checks_{stamp}.html"
    _write_checks_html(html_path, report, judged)
    print(f"wrote {html_path}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--channel", choices=("ner", "hmo", "wikidata"), default="hmo")
    p.add_argument("--evaluator", default=None, choices=(
        "person_ner", "provenance_ner", "contents_ner", "genre_classifier",
        "hmo_wikibase_item", "wikidata_item",
    ))
    p.add_argument("--run-id", default=DEFAULT_RUN_ID)
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--sample", type=int, default=10,
                   help="disagreement rows to embed in the report")
    p.add_argument("--max-claims", type=int, default=8,
                   help="per-item statement checks sampled for wikidata_item")
    p.add_argument("--include-blank-nodes", action="store_true",
                   help="HMO channel: keep non-exportable blank-node drafts")
    p.add_argument("--export-gold", default=None, metavar="PATH",
                   help="write a gold labeling sheet (JSONL) for the scoped "
                        "candidates and exit — no API calls")
    p.add_argument("--balance-total", type=int, default=None, metavar="N",
                   help="with --export-gold: stratified sample of N rows "
                        "balanced across the persisted baseline overall "
                        "classes (full/partial/fail)")
    p.add_argument("--with-evidence", action="store_true",
                   help="with --export-gold: embed MARC context + the full "
                        "candidate payload in each row so a labeler has the "
                        "same evidence the judges see")
    p.add_argument("--silver", action="store_true",
                   help="with --export-gold: pre-fill overall from persisted "
                        "curator approvals (approved=full, rejected=fail)")
    p.add_argument("--gold", default=None, metavar="PATH",
                   help="score Jev against a labeled gold sheet instead of "
                        "tier-1 agreement (tier-1 is skipped unless --tier1-reuse)")
    p.add_argument("--determinism", action="store_true",
                   help="run the Jev pass twice and report verdict stability")
    p.add_argument("--only-gold-keys", action="store_true",
                   help="with --gold: judge only the candidates whose key is "
                        "in the gold sheet (exact certification scope)")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--tier-model", default=DEFAULT_TIER_MODEL)
    p.add_argument("--tier1-reuse", action="store_true",
                   help="load tier-1 verdicts from the latest prior eval-state "
                        "run instead of re-judging (same-fixture reuse)")
    p.add_argument("--rpm", type=int, default=30)
    p.add_argument("--concurrency", type=int, default=5)
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--output-dir", default=str(_REPO / "state" / "typesafe-bakeoff"))
    args = p.parse_args()
    defaults = {"ner": "person_ner", "hmo": "hmo_wikibase_item",
                "wikidata": "wikidata_item"}
    if args.evaluator is None:
        args.evaluator = defaults[args.channel]
    expected = defaults[args.channel]
    ner_set = {"person_ner", "provenance_ner", "contents_ner", "genre_classifier"}
    if args.channel == "ner" and args.evaluator not in ner_set:
        p.error("--channel ner needs an NER evaluator")
    if args.channel != "ner" and args.evaluator != expected:
        p.error(f"--channel {args.channel} needs {expected}")
    raise SystemExit(asyncio.run(_async_main(args)))


if __name__ == "__main__":
    main()
