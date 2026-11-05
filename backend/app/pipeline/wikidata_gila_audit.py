"""Source comparison and review inventory for Gila Prebor's conversion audit."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping

from app.pipeline.marc_ingest import prepare_record_for_pipeline
from app.pipeline.marc_verify_context import canonical_control_number
from converter.wikidata.catalog_notes import is_catalog_note_placeholder
from converter.wikidata.item_builder import manuscript_en_label
from converter.wikidata.work_identity import reference_control_numbers

_REVIEW_CLASSES = (
    "absent_in_source", "present_not_transferred", "identity_or_scope_uncertain",
)


def _rows(value: object) -> list[Mapping[str, object]]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
        raise ValueError("An audit row collection must contain objects.")
    return list(value)


def _text(value: object) -> str:
    return " ".join(str(value or "").replace('""', '"').split()).strip('" ').rstrip(" ,.;:/-")


def _claims(item: Mapping[str, object], pid: str) -> list[Mapping[str, object]]:
    return [row for row in _rows(item.get("statements"))
            if (row.get("property") or row.get("property_id")) == pid]


def _qualified(statement: Mapping[str, object], pid: str, value: str) -> bool:
    return any((q.get("property") or q.get("property_id")) == pid and q.get("value") == value
               for q in _rows(statement.get("qualifiers")))


def build_gila_audit_report(
    marc_records: Iterable[Mapping[str, object]],
    items: Iterable[Mapping[str, object]],
    *,
    qids_by_cn: Mapping[str, str] | None = None,
    live_entities: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Compare local proposals with sources; never report a live correction."""
    from converter.wikidata.content_projection import _work_title_key  # noqa: PLC0415

    materialized = list(items)
    manuscripts: dict[str, Mapping[str, object]] = {}
    works_by_cn: dict[str, list[Mapping[str, object]]] = {}
    for item in materialized:
        records = item.get("records") or []
        if not isinstance(records, list):
            raise ValueError("An item's records must be a list.")
        for raw_cn in records:
            cn = canonical_control_number(raw_cn)
            if item.get("entity_type") == "work":
                works_by_cn.setdefault(cn, []).append(item)
                continue
            if item.get("entity_type") != "manuscript":
                continue
            if cn in manuscripts:
                raise ValueError(f"Multiple proposed manuscripts have source {cn}.")
            manuscripts[cn] = item

    rows: list[dict[str, object]] = []
    totals: Counter[str] = Counter()
    source_authors: dict[str, list[str]] = {}
    for raw in marc_records:
        record = prepare_record_for_pipeline(dict(raw))
        cn = canonical_control_number(record.get("_control_number"))
        if not cn:
            raise ValueError("An audit source must have a control number.")
        item = manuscripts.get(cn, {})
        cases: list[dict[str, object]] = []

        def case(
            point: int, classification: str, field: str, reason: str,
            *, target: list[dict[str, object]] = cases,
        ) -> None:
            target.append({"point": point, "classification": classification,
                           "field": field, "reason": reason})
            totals[classification] += 1

        if not item:
            case(9, "present_not_transferred", "record", "manuscript_absent_from_proposal")
        shelfmark = _text(record.get("holding_942_shelfmark"))
        if shelfmark:
            if shelfmark not in {_text(s.get("value")) for s in _claims(item, "P217")}:
                case(1, "present_not_transferred", "942$z", "current_shelfmark_differs")
            expected_label = manuscript_en_label(
                shelfmark, _text(record.get("holding_942_name")), record,
            )
            labels = item.get("labels") or {}
            if not isinstance(labels, Mapping) or labels.get("en") != expected_label:
                case(1, "present_not_transferred", "942", "structured_english_label_differs")
        else:
            if _text(raw.get("942$z")):
                case(1, "identity_or_scope_uncertain", "942$z", "current_holding_requires_review")
            else:
                case(1, "absent_in_source", "942$z", "no_current_942_shelfmark_in_source")

        if not record.get("extent") and record.get("907$n"):
            case(2, "identity_or_scope_uncertain", "907$n", "image_name_does_not_prove_form")
        if not any(s.get("value") == "Q118384267" and _qualified(s, "P3831", "Q60474998")
                   for s in _claims(item, "P361")):
            case(4, "present_not_transferred", "001", "qualified_ktiv_membership_absent")

        scribes = [row for row in _rows(record.get("contributors"))
                   if str(row.get("role") or "").casefold() in {"scribe", "transcriber"}]
        projected_scribes = _claims(item, "P11603")
        for scribe in scribes:
            name = _text(scribe.get("name"))
            named = [s for s in projected_scribes if any(
                (q.get("property") or q.get("property_id")) == "P1932"
                and _text(q.get("value")) == name for q in _rows(s.get("qualifiers"))
            )]
            if any(s.get("value_type") == "somevalue" for s in named):
                case(5, "identity_or_scope_uncertain", "700", "named_scribe_identity_unresolved")
            elif not named and not projected_scribes:
                case(5, "present_not_transferred", "700", "source_scribe_claim_absent")
            elif not named:
                case(5, "identity_or_scope_uncertain", "700", "verify_scribe_target_identity")
        source_authors[cn] = [_text(row.get("name")) for row in _rows(record.get("authors"))]
        for work in works_by_cn.get(cn, []):
            if (
                any(_work_title_key(s.get("value")) == _work_title_key(record.get("title"))
                    for s in _claims(work, "P1476"))
                and _claims(work, "P2093") and not _claims(work, "P50")
            ):
                case(5, "identity_or_scope_uncertain", "100", "author_identity_not_linked")

        evidence = _rows(item.get("work_candidate_evidence"))
        for candidate in evidence:
            if candidate.get("accepted") is not True:
                case(6, "identity_or_scope_uncertain", str(candidate.get("source_field") or ""),
                     str(candidate.get("reason") or "work_candidate_requires_review"))
            elif candidate.get("identity_scope") == "record":
                case(3, "identity_or_scope_uncertain", str(candidate.get("source_field") or ""),
                     "descriptive_work_identity_is_source_scoped")

        if record.get("place") and not _claims(item, "P1071"):
            case(7, "identity_or_scope_uncertain", "751/260", "production_place_not_resolved")
        source_extent = _text(record.get("extent_text") or raw.get("300$a") or raw.get("943$a"))
        if not record.get("extent"):
            if source_extent:
                case(7, "identity_or_scope_uncertain", "300$a", "physical_extent_not_numeric")
            else:
                case(7, "absent_in_source", "300$a", "no_physical_extent_in_source")
        if len(record.get("languages") or []) > 1 and any(
            "באיטלקית באותיות לטיניות" in str(note) for note in record.get("notes") or []
        ):
            case(7, "identity_or_scope_uncertain", "041/500", "verify_language_scope_against_scan")

        colophon = _text(record.get("colophon_text"))
        if colophon and not is_catalog_note_placeholder(colophon):
            transcriptions = [
                s for s in _claims(item, "P1684") if _qualified(s, "P3831", "Q372474")
            ]
            if not any(colophon in _text(s.get("value")) for s in transcriptions):
                case(8, "present_not_transferred", "957/590", "full_qualified_colophon_absent")
        elif colophon:
            case(8, "identity_or_scope_uncertain", "957/590", "catalog_prose_requires_review")
        for reference in _rows(record.get("catalog_references")):
            catalog = _text(reference.get("catalog"))
            if catalog and catalog not in {_text(s.get("value")) for s in _claims(item, "P528")}:
                case(8, "present_not_transferred", "581/942", "catalog_reference_absent")

        rows.append({
            "control_number": cn, "qid": (qids_by_cn or {}).get(cn),
            "local_id": item.get("local_id"), "status": "local_proposal",
            "record_status_notes": list(record.get("record_status_notes") or []),
            "source_extent_text": source_extent,
            "cases": cases,
        })

    repair_proposals: list[dict[str, object]] = []
    for qid, raw_entity in (live_entities or {}).items():
        if not isinstance(raw_entity, Mapping):
            raise ValueError("A live entity must be an object.")
        claims = raw_entity.get("claims") or {}
        if not isinstance(claims, Mapping):
            raise ValueError("Live claims must be an object.")
        authors = [*_rows(claims.get("P2093")), *_rows(claims.get("P50"))]
        if len(authors) < 2:
            continue
        claim_rows: list[dict[str, object]] = []
        for claim in authors:
            mainsnak = claim.get("mainsnak") or {}
            if not isinstance(mainsnak, Mapping):
                raise ValueError("A live claim must contain a mainsnak.")
            datavalue = mainsnak.get("datavalue") or {}
            if not isinstance(datavalue, Mapping):
                raise ValueError("A live datavalue must be an object.")
            controls = sorted(reference_control_numbers(claim.get("references")))
            claim_rows.append({"claim_id": claim.get("id"), "value": datavalue.get("value"),
                               "source_control_numbers": controls})
        controls = sorted({
            cn for claim in claim_rows
            if isinstance(claim["source_control_numbers"], list)
            for cn in claim["source_control_numbers"]
        })
        repair_proposals.append({
            "qid": qid, "revision": raw_entity.get("lastrevid"),
            "action": "review_source_partition", "claims": claim_rows,
            "source_authors": {cn: source_authors.get(cn, []) for cn in controls},
            "requires_review": True,
        })
    return {
        "mode": "read_only_local_proposal", "live_verified": False,
        "model_decision": "separate_physical_manuscripts_and_written_works",
        "record_count": len(rows), "review_classes": list(_REVIEW_CLASSES),
        "case_counts": {category: totals[category] for category in _REVIEW_CLASSES},
        "records": rows,
        "checked_qids": sorted({str(row["qid"]) for row in rows if row["qid"]}),
        "corrected_qids": [], "work_repair_proposals": repair_proposals,
        "remaining_checks": [
            "verify_dates_places_languages_and_physical_scope",
            "confirm_work_identities_and_authority_links",
            "review_unqualified_or_truncated_transcriptions",
            "inspect_per_item_upload_outcomes_and_live_reference_blocks",
            "read_back_live_claims_before_marking_qids_corrected",
        ],
    }
