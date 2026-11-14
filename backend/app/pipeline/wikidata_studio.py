"""Wikidata Studio backend — uses the *real* desktop item builder.

The desktop pipeline's ``converter.wikidata.item_builder.WikidataItemBuilder``
turns authority-enriched manuscript records into ``WikidataItem`` objects
covering every property the desktop ships with (P31 manuscript class,
P50 author, P136 genre, P407 language, …). The desktop pipeline's
``converter.wikidata.quickstatements.QuickStatementsExporter`` then
serialises them to the QuickStatements v2 text format.

This module is the thin glue: it stitches a run's records + its
*approved* authority matches into the desktop builder's input shape,
runs the builder in a threadpool (it's sync, by design), and returns
both the structured items and the QuickStatements blob.

NO logic is re-implemented here — every fix the desktop pipeline lands
(safety guards, Rule 23 / 38 modification gate, Rule 42 multi-P31,
Rule 47 work-item CREATE ordering, …) is one ``cp`` away.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select

from app.pipeline.ai_verdict_cache_common import strip_volatile_metadata
from app.pipeline.marc_verify_context import canonical_control_number

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.extraction_approval import ExtractionApproval
    from app.models.item_override import WikidataItemOverride
    from app.models.run import AuthorityMatch, RunRecord

logger = logging.getLogger(__name__)

# v5 invalidates pre-notability cache rows that could expose an identifierless
# person as an upload candidate (the current builder omits those rows).
# v6: designation labels, audited holders, anchored works, corrected P/Q constants
# (Rules W-161 … W-166) — every cached Studio row predates them.
# v8: source-specific work authors, person-language suppression, and fuzzy-QID guard.
# v9: folio-range and person-genealogy work-candidate guards (Rule W-264) —
# cached rows still carrying those degenerate 500 works must not upload.
# v10: Rule W-72 catalog-prose P1684 filter extension (colophon prose,
# workflow/access notes, catalog pointers, scholarly comparison) — cached
# rows still carrying those false inscription claims must not upload.
# v11: collection-level Bible/Tanakh titles no longer map to work QIDs
# (Rule W-70 fail-closed) — cached rows still carrying those links must
# not upload.
# v14: colophon field labels (957/590/500) stripped at ingest and the
# record-author fallback limited to the main or sole content work
# (Rule W-300) — cached rows built before those fixes must not upload.
WIKIDATA_STUDIO_BUILD_SCHEMA = "source-aware-works-v24"


def _fingerprint_hash(value: Any) -> str:
    if isinstance(value, dict):
        stable_keys = strip_volatile_metadata(dict.fromkeys(value))
        value = {key: value[key] for key in stable_keys}
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()


def _fingerprint_match(match: Any) -> tuple[str, str]:
    fields = (
        "control_number", "entity_text", "entity_kind", "role", "matched_name",
        "mazal_id", "viaf_id", "wikidata_qid", "confidence", "source", "payload", "approved",
    )
    return str(match.id), _fingerprint_hash({name: getattr(match, name, None) for name in fields})


def _fingerprint_entity(entity: Any) -> tuple[str, str]:
    fields = (
        "control_number", "source", "text", "start", "end", "type", "role",
        "confidence", "model_confidence", "override_text", "override_type", "override_role", "approved",
    )
    return str(entity.id), _fingerprint_hash({name: getattr(entity, name, None) for name in fields})


def _fingerprint_override(override: Any) -> tuple[str, str, bool | None, str]:
    """Override tuple shared by the sequential and streamed builds.

    ``accepted_foreign_qid`` changes the reconciled QID that the builder
    emits, so it must stale the cache. ``ai_verdict`` is deliberately absent.
    """
    return (
        str(override.id), override.local_id, override.approved,
        _fingerprint_hash({
            "labels": override.labels, "descriptions": override.descriptions,
            "aliases": override.aliases, "add_statements": override.add_statements,
            "remove_statements": override.remove_statements,
            "statement_edits": override.statement_edits,
            "accepted_foreign_qid": override.accepted_foreign_qid,
            **({"person_identity_choice": override.person_identity_choice}
               if getattr(override, "person_identity_choice", None) else {}),
            **({"source_review_choices": override.source_review_choices}
               if getattr(override, "source_review_choices", None) else {}),
        }),
    )


def studio_cache_has_stale_validation(
    result_items: list[dict[str, Any]] | None,
) -> bool:
    """Detect stale validation and reference-provenance shapes in a cache.

    ``NO_IDENTIFIER`` is an ERROR for a person and the current builders either
    attach an external identifier or omit the person. A cache containing that
    issue therefore cannot be a valid result for either Studio projection.
    Flat multi-snak sources do not prove their original reference groups.
    """
    for item in result_items or []:
        if not isinstance(item, dict):
            return True
        if reference_provenance_error(item):
            return True
        if str(item.get("entity_type") or "").lower() != "person":
            continue
        issues = item.get("validation_issues")
        if not isinstance(issues, list):
            continue
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            if (
                str(issue.get("code") or "") == "NO_IDENTIFIER"
                and str(issue.get("severity") or "").lower() == "error"
            ):
                return True
    return False


def reference_provenance_error(item: dict[str, Any]) -> bool:
    from converter.wikidata.quickstatements import validate_source_references  # noqa: PLC0415

    statements = item.get("statements", [])
    if not isinstance(statements, list):
        return True
    for statement in statements:
        if not isinstance(statement, dict):
            return True
        try:
            validate_source_references(
                statement.get("references", []), statement.get("reference_blocks", []),
                allow_local=True, require_proven_blocks=True,
            )
        except (TypeError, ValueError):
            return True
    return False


def validate_upload_reference_blocks(items: list[Any]) -> None:
    from converter.wikidata.quickstatements import validate_source_references  # noqa: PLC0415

    for item in items:
        for statement in item.statements:
            validate_source_references(
                statement.references, statement.reference_blocks,
                allow_local=True, require_proven_blocks=True,
            )


async def hmo_instance_qids_for_run(
    db: "AsyncSession", run_id: uuid.UUID, control_numbers: list[str],
) -> dict[str, str]:
    """``control_number -> live HMO Wikibase QID`` for manuscripts this
    run has already uploaded via HMO Wikibase Studio (Phase 5/6 —
    dev-docs/hmo-wikibase-studio-plan.md).

    Computes each manuscript's expected RDF subject URI the same way
    ``converter.transformer.uri_generator.UriGenerator.manuscript_uri``
    does at RDF-build time, then looks it up against this run's
    ``wikibase_entity_mappings`` instance rows. Manuscripts with no
    matching row (not yet built/uploaded) are simply absent from the
    result — callers fall back to the static slug URL for those.
    """
    from converter.config.namespaces import HM  # noqa: PLC0415
    from converter.transformer.uri_generator import UriGenerator  # noqa: PLC0415

    from app.models.wikibase_entity_mapping import (  # noqa: PLC0415
        ENTITY_KIND_INSTANCE,
        WikibaseEntityMapping,
    )
    from app.pipeline.hmo_wikidata_projection import (  # noqa: PLC0415
        canonical_hmo_instance_qids,
    )

    canonical_control_numbers = sorted({
        canonical_control_number(control_number)
        for control_number in control_numbers
        if canonical_control_number(control_number)
    })
    if not canonical_control_numbers:
        return {}

    uri_gen = UriGenerator(namespace=str(HM))
    uri_to_cn = {
        str(uri_gen.manuscript_uri(cn)): cn for cn in canonical_control_numbers
    }

    rows = (
        await db.execute(
            select(
                WikibaseEntityMapping.ontology_uri, WikibaseEntityMapping.wikibase_id,
            ).where(
                WikibaseEntityMapping.run_id == run_id,
                WikibaseEntityMapping.entity_kind == ENTITY_KIND_INSTANCE,
                WikibaseEntityMapping.ontology_uri.in_(uri_to_cn.keys()),
            )
        )
    ).all()
    return canonical_hmo_instance_qids(
        rows,
        {control_number: uri for uri, control_number in uri_to_cn.items()},
    )


def compute_build_fingerprint(
    records: list["RunRecord"],
    all_matches: list["AuthorityMatch"],
    entity_rows: list["ExtractionApproval"],
    override_rows: list["WikidataItemOverride"],
    approved_only: bool,
    hmo_instance_qids: dict[str, str] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> str:
    """SHA-256 fingerprint of everything that feeds the Wikidata item builder.

    Changing any approval flag, override field, match payload, or NER
    text will produce a different fingerprint and trigger a rebuild on
    the next request. ``hmo_instance_qids`` (Phase 6) is included so a
    manuscript getting uploaded to the HMO Wikibase after this run's
    Wikidata Studio cache was built invalidates the cache — otherwise
    P2888/P973 would keep pointing at the stale slug URL until some
    unrelated field changed.
    """
    total = max(
        len(records) + len(all_matches) + len(entity_rows) + len(override_rows),
        1,
    )
    seen = 0

    def tick() -> None:
        nonlocal seen
        seen += 1
        if on_progress is not None and (seen == total or seen % 25 == 0):
            on_progress(min(seen, total), total)

    record_parts: list[tuple[str, str]] = []
    for record in records:
        record_parts.append((record.control_number, _fingerprint_hash(record.marc or {})))
        tick()
    match_parts: list[tuple[str, str]] = []
    for match in all_matches:
        match_parts.append(_fingerprint_match(match))
        tick()
    entity_parts: list[tuple[str, str]] = []
    for entity in entity_rows:
        entity_parts.append(_fingerprint_entity(entity))
        tick()
    override_parts: list[tuple[str, str, bool | None, str]] = []
    for override in override_rows:
        override_parts.append(_fingerprint_override(override))
        tick()
    parts = {
        "build_schema": WIKIDATA_STUDIO_BUILD_SCHEMA,
        "approved_only": approved_only,
        "records": sorted(record_parts),
        "hmo_instance_qids": sorted((hmo_instance_qids or {}).items()),
        "matches": sorted(match_parts),
        "entities": sorted(entity_parts),
        "overrides": sorted(override_parts),
    }
    return hashlib.sha256(
        json.dumps(parts, sort_keys=True, default=str).encode()
    ).hexdigest()


async def build_items_for_run(
    *, marc_records: list[dict[str, Any]],
    approved_matches: list[dict[str, Any]],
    entities_by_cn: dict[str, list[dict[str, Any]]] | None = None,
    return_native: bool = False,
    overrides: dict[str, dict[str, Any]] | None = None,
    hmo_instance_qids: dict[str, str] | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Build Wikidata items + QuickStatements for *marc_records* enriched
    with *approved_matches* and Stage-2 NER *entities*.

    ``entities_by_cn`` maps control_number → list of entity dicts in
    the desktop's expected shape (``source`` ∈ {``person_ner``,
    ``provenance_ner``, ``contents_ner``}, ``type``, ``text``,
    ``start``, ``end``, ``role``, ``confidence``). The desktop builder
    reads ``record["entities"]`` to drive work-item creation
    (contents_ner WORK + FOLIO + WORK_AUTHOR — see Rule 47) and the
    P127/P571 provenance fallbacks. Without this merge, the studio
    creates zero work items (the rest of the pipeline's authority
    surface comes through ``marc_authority_matches``).

    ``should_cancel`` is a sync callable (runs on the worker thread) that
    raises when the curator cancelled; it is tested at every record
    boundary so a cancel stops the build mid-loop (Rule R28).

    Returns::

        {
            "items":           [ <dict-of-WikidataItem>, … ],
            "quickstatements": "CREATE\\nLAST  …",
            "summary":         { "total_items":int, "manuscripts":int,
                                  "persons":int, "works":int,
                                  "statements":int },
        }
    """
    enriched = prepare_enriched_records(
        marc_records, approved_matches, entities_by_cn or {},
    )
    return await run_in_threadpool(
        _build_sync, enriched, return_native, overrides or {}, hmo_instance_qids or {},
        progress_cb, should_cancel,
    )


def prepare_enriched_records(
    marc_records: list[dict[str, Any]],
    approved_matches: list[dict[str, Any]],
    entities_by_cn: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Build the desktop's expected per-record input shape.

    Each record carries its own ``marc_authority_matches`` list of
    *approved* matches (mirroring what authority_enriched.json holds
    in the desktop pipeline) plus its NER ``entities`` (mirroring
    ner_results.json + the desktop's _merge_ner_into_records step).
    Shared by the sequential build and the Modal shard runner so both
    paths feed the builder byte-identical inputs.
    """
    by_cn: dict[str, list[dict[str, Any]]] = {}
    for match in approved_matches:
        control_number = canonical_control_number(match.get("control_number"))
        if control_number:
            by_cn.setdefault(control_number, []).append(match)
    ents_by_cn: dict[str, list[dict[str, Any]]] = {}
    for control_number, entities in entities_by_cn.items():
        canonical = canonical_control_number(control_number)
        if canonical:
            ents_by_cn.setdefault(canonical, []).extend(entities)

    from app.pipeline.marc_ingest import prepare_record_for_pipeline  # noqa: PLC0415

    enriched: list[dict[str, Any]] = []
    for rec in marc_records:
        out = prepare_record_for_pipeline(rec)
        cn = canonical_control_number(
            out.get("_control_number")
            or out.get("control_number")
            or out.get("controlNumber")
            or ""
        )
        out["_control_number"] = cn
        out["authors"]      = _to_dict_list(out.get("authors"),      default_role="author",      default_field="100")
        out["contributors"] = _to_dict_list(out.get("contributors"), default_role="contributor", default_field="700")
        out["subjects"]     = _to_dict_list(out.get("subjects"),     default_role="subject",     default_field="600")
        out["marc_authority_matches"] = [
            _approved_match_to_desktop_shape(m) for m in by_cn.get(cn, [])
        ]
        out["kima_places"] = {
            m["entity_text"]: f"https://www.wikidata.org/entity/{m['wikidata_qid']}"
            for m in by_cn.get(cn, [])
            if (m.get("payload") or {}).get("kima_id")
            and m.get("wikidata_qid")
            and m.get("entity_text")
        }
        # Merge NER entities. Desktop _merge_ner_into_records uses
        # setdefault, so we don't clobber a record that arrived with
        # its own entities list (rare on the web — _to_dict_list above
        # already canonicalises authors/contributors/subjects, but the
        # ``entities`` channel is distinct).
        existing = out.get("entities") or []
        out["entities"] = list(existing) + ents_by_cn.get(cn, [])
        enriched.append(out)

    return enriched


def _build_native_sync(
    records: list[dict[str, Any]],
    overrides: dict[str, dict[str, Any]] | None = None,
    hmo_instance_qids: dict[str, str] | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], None] | None = None,
) -> list[Any]:
    """Run the desktop builder over *records* and apply curator overrides.

    Returns native ``WikidataItem`` objects WITHOUT the corpus-wide
    post-passes — those live in :func:`finish_native_items` so the
    sharded build can run them over the merged corpus.
    """
    from converter.wikidata.item_builder import WikidataItemBuilder  # noqa: PLC0415

    builder = WikidataItemBuilder(  # SPARQL-free for the web
        reconciler=None, hmo_instance_qids=hmo_instance_qids,
    )
    record_cb = progress_cb
    if should_cancel is not None:
        def record_cb(done: int, total: int) -> None:
            if progress_cb is not None:
                progress_cb(done, total)
            should_cancel()

    items = builder.build_all(records, progress_cb=record_cb)
    if should_cancel is not None:
        should_cancel()

    # Apply per-item curator overrides FIRST so validation reflects the
    # final curator-adjusted state (not the raw builder output).
    if overrides:
        for it in items:
            ov = overrides.get(_local_id_for(it))
            if ov:
                _apply_override(it, ov)
                if ov.get("source_review_choices"):
                    from app.pipeline.wikidata_source_choices import apply_choices

                    apply_choices(it, ov["source_review_choices"], {
                        str(record.get("_control_number") or record.get("001") or ""): record for record in records
                    })
    return items


def finish_native_items(items: list[Any]) -> dict[str, Any]:
    """Corpus-wide post-passes + validation + QS export + serialisation.

    Shared by the sequential build and the sharded build: every pass
    here reads or rewrites the whole item corpus, so it must run once
    over the merged corpus, never per shard.
    """
    from app.pipeline.wikidata_canonical_enrichment import (
        normalize_work_author_claims,
    )
    from app.pipeline.wikidata_live_native_hygiene import (  # noqa: PLC0415
        sanitize_studio_items_for_live,
    )
    from app.pipeline.wikidata_local_refs import (  # noqa: PLC0415
        drop_orphan_significant_person_claims,
        drop_redundant_unknown_text_exemplars,
        resolve_local_references,
    )
    from converter.wikidata.item_validator import validate_item  # noqa: PLC0415
    from converter.wikidata.quickstatements import QuickStatementsExporter  # noqa: PLC0415

    normalize_work_author_claims(items)

    # Every ``__LOCAL:`` target must name an item this build produced — works
    # dropped by the thin-title / evidence gates otherwise leave the referring
    # manuscript pointing at nothing (Rule W-138).
    local_ref_stats = resolve_local_references(items)
    drop_orphan_significant_person_claims(items)
    drop_redundant_unknown_text_exemplars(items)

    sanitize_studio_items_for_live(items)

    # Validate every built item, log issues, and collect them per item so
    # the UI can surface inline validation pills without a second round-trip.
    per_item_issues: list[list[dict]] = []
    for it in items:
        raw_issues = validate_item(it)
        issue_dicts: list[dict] = []
        for issue in raw_issues:
            sev = issue.severity if hasattr(issue, "severity") else str(issue)
            code = issue.code if hasattr(issue, "code") else ""
            msg = issue.message if hasattr(issue, "message") else str(issue)
            log_fn = logger.error if sev == "error" else logger.warning
            log_fn("validate_item [%s] %s: %s", sev.upper(), code, msg)
            issue_dicts.append({"code": code, "severity": sev, "message": msg})
        per_item_issues.append(issue_dicts)

    exporter = QuickStatementsExporter()
    qs_text = exporter.export(items)

    summary = {
        "total_items": len(items),
        "manuscripts": sum(1 for i in items if getattr(i, "entity_type", "") == "manuscript"),
        "persons":     sum(1 for i in items if getattr(i, "entity_type", "") == "person"),
        "works":       sum(1 for i in items if getattr(i, "entity_type", "") == "work"),
        "statements":  sum(len(getattr(i, "statements", []) or []) for i in items),
        "local_references_degraded": local_ref_stats["degraded"],
        "local_references_dropped": local_ref_stats["dropped"],
    }

    serialised = [_serialise_item(it) for it in items]
    for d, vi in zip(serialised, per_item_issues):
        d["validation_issues"] = vi

    return {
        "items": serialised,
        "quickstatements": qs_text,
        "summary": summary,
    }


def _build_sync(
    records: list[dict[str, Any]],
    return_native: bool = False,
    overrides: dict[str, dict[str, Any]] | None = None,
    hmo_instance_qids: dict[str, str] | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], None] | None = None,
) -> dict[str, Any]:
    items = _build_native_sync(
        records, overrides, hmo_instance_qids, progress_cb, should_cancel,
    )
    result = finish_native_items(items)
    result["native_items"] = items if return_native else None
    return result


# ── Sharded-build helpers (batch-build parity with rdf_build_shard) ──────


def native_item_payload(item: Any) -> dict[str, Any]:
    """Lossless JSON-safe snapshot of a native ``WikidataItem``.

    Both item and statement are plain dataclasses with JSON-safe field
    types, so ``asdict`` + the shard's JSON transport round-trips
    exactly. The orchestrator rebuilds native items with
    :func:`native_item_from_payload` before the corpus-wide finish.
    """
    from dataclasses import asdict  # noqa: PLC0415

    data = _coerce(asdict(item))
    from converter.wikidata.quickstatements import reference_data  # noqa: PLC0415

    for statement, raw in zip(item.statements, data.get("statements", []), strict=True):
        references, blocks = reference_data(
            statement.references, getattr(statement, "reference_blocks", []),
        )
        if blocks and "reference_blocks" not in raw:
            raise ValueError("WikidataStatement cannot serialize reference_blocks")
        raw["references"] = references
    return data


def native_item_from_payload(payload: dict[str, Any]) -> Any:
    """Rebuild a native ``WikidataItem`` from :func:`native_item_payload`."""
    from dataclasses import fields  # noqa: PLC0415

    from converter.wikidata.item_models import (  # noqa: PLC0415
        WikidataItem,
        WikidataStatement,
    )
    from converter.wikidata.quickstatements import statement_reference_kwargs  # noqa: PLC0415

    stmt_fields = {f.name for f in fields(WikidataStatement)}
    raw_statements = payload.get("statements", [])
    if not isinstance(raw_statements, list) or any(not isinstance(s, dict) for s in raw_statements):
        raise ValueError("statements must be a list of objects")
    statements = [
        WikidataStatement(**{
            **{k: v for k, v in s.items()
               if k in stmt_fields and k not in {"references", "reference_blocks"}},
            **statement_reference_kwargs(s, WikidataStatement),
        })
        for s in raw_statements
    ]
    item_fields = {f.name for f in fields(WikidataItem)}
    data = {k: v for k, v in payload.items() if k in item_fields}
    data["statements"] = statements
    return WikidataItem(**data)


def merge_shard_items(shards: list[list[Any]]) -> list[Any]:
    """Merge shard-built native items into one corpus (build order).

    Duplicate identities must agree on claims and source scope. Matching
    claims retain complete source blocks from each shard.
    """
    from copy import deepcopy  # noqa: PLC0415

    from converter.wikidata.item_builder import _is_anonymous_name  # noqa: PLC0415
    from converter.wikidata.item_models import (  # noqa: PLC0415
        merge_statement_references,
        snak_identity,
    )
    from converter.wikidata.quickstatements import validate_source_references  # noqa: PLC0415
    from converter.wikidata.work_author_claims import (  # noqa: PLC0415
        promote_safe_work_author_claims,
    )
    from converter.wikidata.work_identity import author_name_key  # noqa: PLC0415
    from converter.wikidata.semantic_policy import DEFAULT_SEMANTIC_POLICY  # noqa: PLC0415

    def claims_for(item: Any) -> dict[str, Any]:
        claims: dict[str, Any] = {}
        for statement in item.statements:
            validate_source_references(
                statement.references, statement.reference_blocks,
                allow_local=True, require_proven_blocks=True,
            )
            data = asdict(statement)
            data.pop("references")
            data.pop("reference_blocks")
            if not isinstance(statement.qualifiers, list) or any(
                not isinstance(qualifier, dict) for qualifier in statement.qualifiers
            ):
                raise ValueError("Shard statement qualifiers must be a list of objects")
            data["qualifiers"] = sorted(snak_identity(q) for q in statement.qualifiers)
            key = json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False)
            if key in claims:
                merge_statement_references(claims[key], statement)
            else:
                claims[key] = deepcopy(statement)
        return claims

    manuscripts: list[Any] = []
    persons: dict[str, Any] = {}
    works: dict[str, Any] = {}
    for shard in shards:
        for item in shard:
            entity_type = str(getattr(item, "entity_type", "") or "")
            if entity_type == "manuscript":
                manuscripts.append(item)
                continue
            local_id = str(getattr(item, "local_id", "") or "")
            if not local_id:
                raise ValueError("A sharded entity requires a local ID")
            bucket = persons if entity_type == "person" else works
            other_bucket = works if entity_type == "person" else persons
            if local_id in other_bucket:
                raise ValueError(f"Shard item {local_id} has conflicting entity types")
            existing = bucket.get(local_id)
            if existing is None:
                bucket[local_id] = deepcopy(item)
                continue
            if (entity_type != existing.entity_type or item.existing_qid != existing.existing_qid
                    or item.semantic_type != existing.semantic_type
                    or item.heading_mismatch != existing.heading_mismatch):
                raise ValueError(f"Shard item {local_id} has conflicting identity")
            if entity_type not in {"person", "work"}:
                raise ValueError(f"Shard item {local_id} has an unsupported identity type")
            if item.existing_qid and (
                not isinstance(item.existing_qid, str)
                or not re.fullmatch(r"Q[1-9][0-9]*", item.existing_qid)
            ):
                raise ValueError(f"Shard item {local_id} has an invalid Wikidata identity")
            if any(
                not isinstance(source.records, list)
                or any(not isinstance(record, str) or not record.strip() for record in source.records)
                for source in (existing, item)
            ):
                raise ValueError(f"Shard item {local_id} has invalid source records")
            existing_claims = claims_for(existing)
            incoming_claims = claims_for(item)
            additional_keys = existing_claims.keys() ^ incoming_claims.keys()
            multi_value_properties = {"P106"} if entity_type == "person" else {"P407", "P136"}
            for property_id in {
                statement.property_id for statement in [
                    *existing_claims.values(), *incoming_claims.values(),
                ]
            }:
                old = {key for key, statement in existing_claims.items()
                       if statement.property_id == property_id}
                new = {key for key, statement in incoming_claims.items()
                       if statement.property_id == property_id}
                if property_id in multi_value_properties:
                    combined = [existing_claims[key] for key in old] + [
                        incoming_claims[key] for key in new
                    ]
                    for index, previous in enumerate(combined):
                        for incoming in combined[index + 1:]:
                            if previous.value == incoming.value and any(
                                getattr(previous, field) != getattr(incoming, field)
                                for field in ("value_type", "rank", "precision", "language", "unit")
                            ):
                                raise ValueError(
                                    f"Shard item {local_id} has conflicting {property_id} claims"
                                )
                elif old and new and old != new:
                    raise ValueError(f"Shard item {local_id} has conflicting {property_id} claims")
                if (property_id in DEFAULT_SEMANTIC_POLICY.strong_external_id_properties
                        and max(len(old), len(new)) > 1):
                    raise ValueError(f"Shard item {local_id} has conflicting {property_id} claims")
            records = set(existing.records) & set(item.records)
            if entity_type == "work":
                for property_id in ("P1476", "P50", "P2093"):
                    if {key for key, statement in existing_claims.items()
                        if statement.property_id == property_id} != {
                        key for key, statement in incoming_claims.items()
                        if statement.property_id == property_id
                    }:
                        raise ValueError(f"Shard work {local_id} has conflicting title or authors")
                evidence = [*existing.work_candidate_evidence, *item.work_candidate_evidence]
                if any(not isinstance(row, dict) for row in evidence):
                    raise ValueError(f"Shard work {local_id} has invalid identity evidence")
                evidence_names: list[set[str]] = []
                for source in (existing, item):
                    names: set[str] = set()
                    for row in source.work_candidate_evidence:
                        if row.get("accepted") is False:
                            continue
                        name = row.get("author_name")
                        if name is not None and not isinstance(name, str):
                            raise ValueError(f"Shard work {local_id} has invalid author evidence")
                        source_text = row.get("source_text")
                        if (not name and row.get("source_field") == "100/245"
                                and isinstance(source_text, str) and " \u2014 " in source_text):
                            name = source_text.rsplit(" \u2014 ", 1)[1]
                        if name and author_name_key(name):
                            names.add(author_name_key(name))
                    evidence_names.append(names)
                if any(len(names) > 1 for names in evidence_names) or (
                    all(evidence_names) and evidence_names[0] != evidence_names[1]
                ):
                    raise ValueError(f"Shard work {local_id} has conflicting author evidence")
                if any(row.get("identity_scope") == "record" for row in evidence) and (
                    not existing.records or not item.records
                    or set(existing.records) != set(item.records)
                ):
                    raise ValueError(f"Shard work {local_id} has different record-scoped identity")
                titles = [s for s in existing.statements if s.property_id == "P1476" and s.value]
                authors = [
                    s for s in existing.statements
                    if isinstance(s.value, str) and (
                        (s.property_id == "P50"
                         and s.value_type in {"item", "wikibase-item", "wikibase-entityid"}
                         and (re.fullmatch(r"Q[1-9][0-9]*", s.value)
                              or re.fullmatch(r"__LOCAL:(?:viaf|mazal):[0-9]+", s.value)))
                        or (s.property_id == "P2093" and s.value_type == "string"
                            and s.value.strip() and not _is_anonymous_name(s.value))
                    )
                ]
                if not titles or (not records and not authors):
                    raise ValueError(f"Shard work {local_id} has ambiguous identity")
                if additional_keys and not authors:
                    raise ValueError(f"Shard work {local_id} has ambiguous author identity")
            if entity_type == "person":
                identity = re.fullmatch(r"(viaf|mazal):([0-9]+)", local_id)
                if identity:
                    identity_property = "P214" if identity.group(1) == "viaf" else "P8189"
                    if any(str(statement.value) != identity.group(2) for statement in [
                        *existing_claims.values(), *incoming_claims.values(),
                    ] if statement.property_id == identity_property):
                        raise ValueError(f"Shard person {local_id} has a conflicting identifier")
                if not (identity or existing.existing_qid):
                    raise ValueError(f"Shard person {local_id} has ambiguous identity")
                if existing.heading_mismatch or any(
                    not isinstance(row, dict) or row.get("accepted") is False
                    or row.get("guard_flags") or row.get("rejection_reason")
                    for row in [*existing.authority_evidence, *item.authority_evidence]
                ):
                    raise ValueError(f"Shard person {local_id} has untrusted identity evidence")
                shared_languages = {
                    language for language in existing.labels.keys() & item.labels.keys()
                    if existing.labels[language].strip() and item.labels[language].strip()
                }
                if existing.labels and item.labels and not shared_languages:
                    raise ValueError(f"Shard person {local_id} has unverified label identity")
                for language in shared_languages:
                    old_label = " ".join(existing.labels[language].split()).casefold()
                    new_label = " ".join(item.labels[language].split()).casefold()
                    old_aliases = {" ".join(value.split()).casefold()
                                   for value in existing.aliases.get(language, [])}
                    new_aliases = {" ".join(value.split()).casefold()
                                   for value in item.aliases.get(language, [])}
                    if old_label != new_label and new_label not in old_aliases and (
                        old_label not in new_aliases
                    ):
                        raise ValueError(f"Shard person {local_id} has conflicting labels")
            for key in additional_keys:
                statement = existing_claims.get(key) or incoming_claims[key]
                if (not isinstance(statement.value, str) or not statement.value.strip()
                        or statement.value != statement.value.strip()
                        or any(ord(character) < 32 for character in statement.value)
                        or not (statement.references or statement.reference_blocks)):
                    raise ValueError(f"Shard item {local_id} has an unsafe additional claim")
                identifier = (
                    statement.property_id in DEFAULT_SEMANTIC_POLICY.strong_external_id_properties
                    and statement.value_type == "external-id"
                )
                source_backed_item = (
                    statement.property_id in (
                        {"P106"} if entity_type == "person" else {"P407", "P136"}
                    )
                    and statement.value_type in {"item", "wikibase-item", "wikibase-entityid"}
                    and re.fullmatch(r"Q[1-9][0-9]*", statement.value)
                )
                if not identifier and not source_backed_item:
                    raise ValueError(f"Shard item {local_id} has an unsafe additional claim")
                if statement.property_id in {"P214", "P8189"} and not re.fullmatch(
                    r"[1-9][0-9]*", statement.value,
                ):
                    raise ValueError(f"Shard item {local_id} has an invalid identifier")
                if (entity_type == "person" and statement.property_id == "P8189"
                        and not statement.value.startswith("9870")):
                    raise ValueError(f"Shard person {local_id} has an invalid NLI authority identifier")
            for key, statement in incoming_claims.items():
                if key in existing_claims:
                    merge_statement_references(existing_claims[key], statement)
                else:
                    existing_claims[key] = statement
            existing.statements = list(existing_claims.values())
            existing.records = sorted({*existing.records, *item.records})
            for field_name in ("labels", "descriptions"):
                retained_text = getattr(existing, field_name)
                for language, value in getattr(item, field_name).items():
                    if language not in retained_text and value:
                        retained_text[language] = value
            for field_name in ("authority_evidence", "work_candidate_evidence"):
                retained = getattr(existing, field_name)
                for row in getattr(item, field_name):
                    if row not in retained:
                        retained.append(deepcopy(row))
            for language, aliases in item.aliases.items():
                retained_aliases = existing.aliases.setdefault(language, [])
                for alias in aliases:
                    if alias not in retained_aliases:
                        retained_aliases.append(alias)
    merged = [*works.values(), *persons.values(), *manuscripts]
    promote_safe_work_author_claims(merged)
    return merged


async def build_shard_items(
    *,
    marc_records: list[dict[str, Any]],
    approved_matches: list[dict[str, Any]],
    entities_by_cn: dict[str, list[dict[str, Any]]],
    overrides: dict[str, dict[str, Any]] | None = None,
    hmo_instance_qids: dict[str, str] | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]]:
    """Build one shard's native items and return lossless payloads.

    Same record preparation + builder + override application as the
    sequential path, but without the corpus-wide finish — the
    orchestrator merges shards first, then runs :func:`finish_native_items`.
    """
    enriched = prepare_enriched_records(
        marc_records, approved_matches, entities_by_cn,
    )
    items = await run_in_threadpool(
        _build_native_sync, enriched, overrides or {}, hmo_instance_qids or {},
        progress_cb,
    )
    return [native_item_payload(it) for it in items]


def _approved_match_to_desktop_shape(m: dict[str, Any]) -> dict[str, Any]:
    """Re-shape a web ``authority_matches`` row into the dict the
    desktop ``WikidataItemBuilder`` reads off ``marc_authority_matches``.

    Desktop expects: name, role, field, mazal_id, viaf_uri (full URL),
    wikidata_qid, confidence (high/medium/low or float), sources,
    source_count, birth_year, death_year, preferred_name_lat, gnd_id,
    lc_id, isni, bnf_id, guard_flags, plus the curator's ``approved`` bool.
    """
    payload = m.get("payload") or {}
    cluster = payload.get("cluster_ids") or {}
    # Strip surrounding/trailing quotes (ASCII + Unicode typographic) that
    # MARC parsers sometimes leave in role and name strings — e.g.
    # 'former owner"', '"(מעתיק)"', '"Meir Netiv"' — so ROLE_TO_PID can match
    # and the builder's clean_name path doesn't need to double-strip.
    _Q = '"\'\u201c\u201d\u2018\u2019\u00ab\u00bb\u2039\u203a'
    raw_role = str(m.get("role") or "").strip().strip(_Q).strip()
    raw_name = str(m.get("entity_text") or "").strip().strip(_Q).strip().rstrip(",;:.")
    return {
        "name":               raw_name,
        "role":               raw_role,
        "field":              m.get("field") or payload.get("field") or "700/710/711",
        "entity_kind":        m.get("entity_kind") or payload.get("entity_kind") or "person",
        "main_marc_tag":      payload.get("main_marc_tag") or payload.get("main_marc_field") or "",
        "mazal_id":           m.get("mazal_id", ""),
        "viaf_uri":           (
            f"https://viaf.org/viaf/{m['viaf_id']}"
            if m.get("viaf_id") else ""
        ),
        "wikidata_qid":       m.get("wikidata_qid", ""),
        "confidence":         m.get("confidence", "low"),
        "source":             m.get("source", ""),
        "sources":            payload.get("sources") or [],
        "source_count":       payload.get("source_count") or 1,
        "birth_year":         payload.get("birth_year"),
        "death_year":         payload.get("death_year"),
        "preferred_name_lat": payload.get("preferred_name_lat", ""),
        "preferred_name_heb": payload.get("preferred_name_heb") or "",
        "kima_viaf_id":       payload.get("kima_viaf_id") or "",
        "j9u_id":             (payload.get("cluster_ids") or {}).get("j9u") or "",
        "gnd_id":             cluster.get("gnd", ""),
        "lc_id":              cluster.get("lc", ""),
        "isni":               cluster.get("isni", ""),
        "bnf_id":             cluster.get("bnf", ""),
        # Wikidata enrichment: Hebrew label + English description from SPARQL
        "wikidata_he_label":  payload.get("wikidata_he_label") or "",
        "wikidata_en_description": payload.get("wikidata_en_description") or "",
        # Mazal supplementary IDs
        "mazal_aleph_id":     payload.get("mazal_aleph_id") or "",
        "guard_flags":        payload.get("guard_flags") or [],
        "rejection_reason":   payload.get("rejection_reason") or "",
        "name_type":          payload.get("name_type") or payload.get("viaf_name_type") or "",
        "matched":            1,
        "approved":           bool(m.get("approved", True)),
    }


def _serialise_item(item: Any) -> dict[str, Any]:
    """Best-effort dataclass-to-dict + English-label enrichment.

    Every emitted statement / qualifier / reference snak gets two extra
    fields the UI consumes directly:

        ``property_label`` — e.g. "instance of"  for P31
        ``value_label``    — e.g. "Ktiv (NLI manuscript catalog)" for Q118384267

    Both fall back to the bare PID / QID when the desktop's static
    label dictionary doesn't cover the value. The frontend then lazily
    fetches unknown labels via /api/wikidata/labels and patches in.
    """
    try:
        data = _coerce(asdict(item))
    except Exception:
        data = {
            "labels":       getattr(item, "labels", {}),
            "descriptions": getattr(item, "descriptions", {}),
            "aliases":      getattr(item, "aliases", {}),
            "statements":   [_coerce(s.__dict__) for s in getattr(item, "statements", [])],
            "existing_qid": getattr(item, "existing_qid", None),
            "entity_type":  getattr(item, "entity_type", None),
            "records": getattr(item, "records", []),
            "authority_evidence": getattr(item, "authority_evidence", []),
            "work_candidate_evidence": getattr(item, "work_candidate_evidence", []),
        }

    from converter.wikidata.quickstatements import reference_data  # noqa: PLC0415

    # Enrich every statement (+ its qualifiers + references) with labels.
    for native, stmt in zip(
        getattr(item, "statements", []), data.get("statements") or [], strict=True,
    ):
        if getattr(native, "reference_blocks", []) and "reference_blocks" not in stmt:
            raise ValueError("WikidataStatement cannot serialize reference_blocks")
        references, blocks = reference_data(
            stmt.get("references", []), stmt.get("reference_blocks", []),
        )
        stmt["references"] = references
        if "reference_blocks" in stmt:
            stmt["reference_blocks"] = blocks
        _enrich_snak(stmt)
        for q in stmt.get("qualifiers") or []:
            _enrich_snak(q)
        for r in stmt.get("references") or []:
            # Reference snaks come either as a flat dict or wrapped in
            # {"snaks": [...]} — desktop emits both shapes.
            if isinstance(r, dict):
                _enrich_snak(r)
                for s in r.get("snaks") or []:
                    _enrich_snak(s)
        for block in blocks:
            for snak in block:
                _enrich_snak(snak)
    return data


def _enrich_snak(snak: Any) -> None:
    """In-place: stamp ``property_label`` + ``value_label`` on a
    statement / qualifier / reference dict.

    Only stamp when the desktop's static dictionary actually carries
    the label — never the bare PID/QID. (desktop's ``property_label`` /
    ``qid_label`` helpers fall back to the id string, which would
    short-circuit the frontend's lazy lookup against live Wikidata.
    Q652 was the canonical regression: returned "Q652" instead of
    "Italian", so the lazy store never got a chance to resolve.)
    """
    if not isinstance(snak, dict):
        return
    from converter.wikidata.property_labels import PROPERTY_LABELS, qid_label  # noqa: PLC0415

    prop = snak.get("property") or snak.get("property_id")
    if isinstance(prop, str) and prop:
        # Mirror the PID into ``property`` — the dataclass field is
        # ``property_id``, so every serialised statement used to reach the UI,
        # exports and diagnostics with ``property: null`` (Rule W-62 / W-137).
        snak.setdefault("property", prop)
        snak["property_id"] = prop
        plabel = PROPERTY_LABELS.get(prop)
        if plabel:
            snak["property_label"] = plabel

    # value_label: stamp when the static table OR the audited holding-
    # institution table can gloss the QID. Never fall back to the bare QID
    # string (that short-circuits the frontend's lazy Wikidata lookup).
    val = snak.get("value")
    vid = snak.get("value_id")
    qid = vid if isinstance(vid, str) and vid.startswith("Q") else \
          (val if isinstance(val, str) and val.startswith("Q") and val[1:].isdigit() else None)
    if qid:
        vlabel = qid_label(qid)
        if vlabel and vlabel != qid:
            snak["value_label"] = vlabel


def _to_dict_list(
    value: Any, *, default_role: str, default_field: str,
) -> list[dict[str, Any]]:
    """Desktop's WikidataItemBuilder iterates contributors / authors /
    subjects expecting each entry to be a dict (``entry.get('name', '')``).
    Web uploads sometimes carry plain strings; normalise here.
    """
    if value is None:
        return []
    out: list[dict[str, Any]] = []
    for v in value if isinstance(value, list) else [value]:
        if isinstance(v, dict):
            out.append(v)
        elif isinstance(v, str) and v.strip():
            out.append({"name": v.strip(), "role": default_role, "field": default_field})
    return out


def _coerce(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _coerce(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_coerce(v) for v in value]
    if isinstance(value, tuple):
        return [_coerce(v) for v in value]
    if hasattr(value, "value") and hasattr(value, "name"):  # Enum-ish
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


async def quickstatements_for_items(items: list[Any]) -> str:
    """Generate a QuickStatements TSV blob for an arbitrary list of native
    WikidataItem objects (e.g. after filtering by curator approval)."""
    def _sync() -> str:
        from converter.wikidata.quickstatements import QuickStatementsExporter  # noqa: PLC0415
        return QuickStatementsExporter().export(items)
    return await run_in_threadpool(_sync)


def local_id_for_item(item: Any) -> str:
    """Stable handle for one built item, used to key overrides.

    Matches what the frontend computes per row (entity_type:index works
    in the visible list, but for *persisted* edits we need something
    stable across rebuilds — pick a real identifier instead).
    """
    return _local_id_for(item)


def _local_id_for(item: Any) -> str:
    for attr in ("local_id", "id"):
        v = getattr(item, attr, None)
        if v:
            return str(v)
    # Fallback: stable composite of entity_type + first label + first
    # external ID. Good enough for runs whose contents don't change.
    et = getattr(item, "entity_type", "") or "item"
    labels = getattr(item, "labels", {}) or {}
    label = labels.get("en") or labels.get("he") or next(iter(labels.values()), "")
    # Pull the first external-id-shaped statement value (Mazal, VIAF, Wikidata)
    ext = ""
    for s in getattr(item, "statements", []) or []:
        prop = getattr(s, "property", None) or getattr(s, "property_id", None)
        if prop in ("P8189", "P214", "P244", "P227", "P213"):
            ext = str(getattr(s, "value", "") or getattr(s, "value_id", "") or "")
            break
    return f"{et}::{ext or label or 'unknown'}"


def _apply_override(item: Any, ov: dict[str, Any]) -> None:
    """Shallow-merge a curator override onto a native WikidataItem."""
    labels       = ov.get("labels")       or {}
    descriptions = ov.get("descriptions") or {}
    aliases      = ov.get("aliases")      or {}
    remove       = set(int(i) for i in (ov.get("remove_statements") or []))
    edits        = ov.get("statement_edits") or {}        # {"3": {value: "Q5"}}
    add          = ov.get("add_statements") or []

    if labels:
        cur = getattr(item, "labels", {}) or {}
        cur.update({k: v for k, v in labels.items() if v is not None})
        # remove keys explicitly set to None
        for k, v in labels.items():
            if v is None and k in cur:
                cur.pop(k, None)
        item.labels = cur
    if descriptions:
        cur = getattr(item, "descriptions", {}) or {}
        cur.update({k: v for k, v in descriptions.items() if v is not None})
        for k, v in descriptions.items():
            if v is None and k in cur:
                cur.pop(k, None)
        item.descriptions = cur
    if aliases:
        cur = getattr(item, "aliases", {}) or {}
        for lang, vals in aliases.items():
            if vals is None:
                cur.pop(lang, None)
            else:
                cur[lang] = list(vals)
        item.aliases = cur

    stmts = list(getattr(item, "statements", []) or [])

    # Statement edits — shallow-set attributes on the dataclass.
    for k, patch in edits.items():
        try:
            idx = int(k)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(stmts):
            stmt = stmts[idx]
            for field, value in (patch or {}).items():
                if hasattr(stmt, field):
                    setattr(stmt, field, value)

    # Statement removals (apply after edits so indices stay stable).
    if remove:
        stmts = [s for i, s in enumerate(stmts) if i not in remove]

    # Appended statements must remain native so validation and export see them.
    for raw in add:
        if isinstance(raw, dict):
            stmts.append(_dict_to_stmt(raw))

    item.statements = stmts


def _dict_to_stmt(d: dict[str, Any]) -> Any:
    """Build a native statement from a curator override."""
    from converter.wikidata.item_models import WikidataStatement  # noqa: PLC0415
    from converter.wikidata.property_mapping import PRECISION_YEAR  # noqa: PLC0415
    from converter.wikidata.quickstatements import statement_reference_kwargs  # noqa: PLC0415

    property_id = str(d.get("property_id") or d.get("property") or "").strip()
    if not property_id:
        raise ValueError("A Wikidata statement requires a property ID")
    qualifiers = d.get("qualifiers", [])
    if not isinstance(qualifiers, list) or any(not isinstance(q, dict) for q in qualifiers):
        raise ValueError("Statement qualifiers must be a list of objects")
    precision = d.get("precision")
    return WikidataStatement(
        property_id=property_id,
        value=d.get("value", d.get("value_id")),
        value_type=str(d.get("value_type") or "string"),
        qualifiers=qualifiers,
        **statement_reference_kwargs(d, WikidataStatement),
        precision=int(precision) if precision is not None else PRECISION_YEAR,
        language=str(d.get("language") or "he"),
        unit=str(d.get("unit") or ""),
        rank=str(d.get("rank") or "normal"),
    )
