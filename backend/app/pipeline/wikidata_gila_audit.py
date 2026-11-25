"""Source comparison and review inventory for Gila Prebor's conversion audit."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from unicodedata import normalize

from app.pipeline import marc_occurrences as marc
from app.pipeline.marc_ingest import prepare_record_for_pipeline
from app.pipeline.marc_verify_context import canonical_control_number
from app.pipeline.wikidata_gila_authority_reads import MazalPersonAuthority
from app.pipeline.wikidata_gila_facts import _live_claims, _reference_status, compare_gila_source_facts
from app.pipeline.wikidata_gila_repairs import build_gila_repair_proposals, entity_digest
from app.pipeline.wikidata_source_coverage import source_passages
from converter.wikidata.catalog_notes import is_catalog_note_placeholder
from converter.wikidata.catalog_references import catalog_reference_code, catalog_reference_text
from converter.wikidata.item_builder import manuscript_en_label
from converter.wikidata.work_identity import reference_control_numbers

_REVIEW_CLASSES = (
    "absent_in_source",
    "present_not_transferred",
    "identity_or_scope_uncertain",
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
    return [
        row
        for row in _rows(item.get("statements"))
        if (row.get("property") or row.get("property_id")) == pid
    ]


def _qualified(statement: Mapping[str, object], pid: str, value: str) -> bool:
    return any(
        (q.get("property") or q.get("property_id")) == pid and q.get("value") == value
        for q in _rows(statement.get("qualifiers"))
    )


def current_holding(cn, source):
    if not source:
        return None
    full = prepare_record_for_pipeline(dict(source))
    if canonical_control_number(full.get("_control_number")) != cn:
        return None
    if full.get("holding_subfield_reviews"):
        owners = {
            _text(name).casefold()
            for row in marc.occurrences(full, "710")
            if any(role.casefold().strip(". ") in {"current owner", "בעלים נוכחיים"} for role in marc.values(row, "e"))
            for name in marc.values(row, "a")
        }
        numbered = [
            row for row in marc.occurrences(full, "942")
            if row["indicators"][0] == "1"
            and len(marc.values(row, "a")) == len(marc.values(row, "z")) == 1
            and _text(marc.values(row, "z")[0])
        ]
        if len(numbered) != 1 or _text(marc.values(numbered[0], "a")[0]).casefold() not in owners:
            return None
        full = prepare_record_for_pipeline({**source, "reviewed_holding_occurrence": marc.source_key(numbered[0])})
    name = _text(full.get("holding_942_name"))
    shelfmark = _text(full.get("holding_942_shelfmark"))
    if not name or not shelfmark:
        return None
    exact = [
        row for row in marc.occurrences(full, "942")
        if [_text(value) for value in marc.values(row, "a")] == [name]
        and [_text(value) for value in marc.values(row, "z")] == [shelfmark]
    ]
    if len(exact) != 1:
        return None
    return exact[0], shelfmark, normalize("NFC", manuscript_en_label(shelfmark, name, full))


def current_holding_label(cn, source, live) -> str | None:
    holding = current_holding(cn, source)
    if not holding or not live or {row["value"] for row in _live_claims(live, "P3959")} != {cn}:
        return None
    _, shelfmark, label = holding
    claims = _live_claims(live, "P217")
    if (
        len(claims) != 1 or _text(claims[0]["value"]) != shelfmark
        or _reference_status(claims, cn, live=True) != "source_bound"
    ):
        return None
    return label


def build_gila_audit_report(
    marc_records: Iterable[Mapping[str, object]],
    items: Iterable[Mapping[str, object]],
    *,
    qids_by_cn: Mapping[str, str] | None = None,
    live_entities: Mapping[str, object] | None = None,
    approved_author_qids: Mapping[tuple[str, str], Iterable[str]] | None = None,
    include_repair_payloads: bool = True,
    repair_authority_by_cn: Mapping[str, list] | None = None,
    repair_viaf_sources: Mapping[str, dict] | None = None,
    repair_gnd_sources: Mapping[str, dict] | None = None,
    people_marc: Mapping[str, dict] | None = None,
    full_source_coverage: bool = False,
    repair_lc_headings: Mapping[str, frozenset[str]] | None = None,
    repair_lc_death_years: Mapping[str, str] | None = None,
    repair_mazal_headings: Mapping[str, frozenset[str]] | None = None,
    repair_mazal_people: Mapping[str, MazalPersonAuthority] | None = None,
    repair_catalogue_records: Mapping[tuple[str, str], dict] | None = None,
    repair_linked_bibs: Mapping[str, dict] | None = None,
    repair_linked_frames: Mapping[str, dict] | None = None,
    repair_linked_viaf: Mapping[str, frozenset[tuple[str, str]]] | None = None,
) -> dict[str, object]:
    """Compare local proposals with sources; never report a live correction."""
    from app.pipeline.wikidata_gila_identity_evidence import live_source_match
    from app.pipeline.wikidata_gila_people import people_role_cases
    from app.pipeline.wikidata_gila_sources import full_source_cases, source_colophon
    from converter.wikidata.content_projection import _work_title_key  # noqa: PLC0415

    sources = list(marc_records)
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
    colophons_by_qid: dict[str, str] = {}
    for raw in sources:
        from app.pipeline.wikidata_source_choices import (
            reviewed_catalog_map,
            source_record_for_audit,
        )

        source_cn = canonical_control_number(
            prepare_record_for_pipeline(dict(raw)).get("_control_number")
        )
        record, reviewed_choices = source_record_for_audit(
            manuscripts.get(source_cn, {}), raw, live_entities, approved_author_qids
        )
        reviewed_catalogs = reviewed_catalog_map(reviewed_choices)
        cn = canonical_control_number(record.get("_control_number"))
        if not cn:
            raise ValueError("An audit source must have a control number.")
        item = manuscripts.get(cn, {})
        cases: list[dict[str, object]] = []
        public_qid = (qids_by_cn or {}).get(cn)
        public_item = live_entities.get(public_qid) if live_entities and public_qid else None

        def case(
            point: int,
            classification: str,
            field: str,
            reason: str,
            *,
            target: list[dict[str, object]] = cases,
            verified: bool = False,
        ) -> None:
            target.append(
                {"point": point, "classification": classification, "field": field, "reason": reason,
                 **({"verified": True} if verified else {})}
            )
            totals[classification] += 1

        if not item:
            case(9, "present_not_transferred", "record", "manuscript_absent_from_proposal")
        holding_review = bool(record.get("holding_subfield_reviews"))
        full_people_source = (people_marc or {}).get(cn)
        expected_holding_label = None
        if (full_source_coverage and (holding_review or not record.get("holding_942_shelfmark"))
            and not record.get("reviewed_holding_occurrence")):
            expected_holding_label = current_holding_label(cn, full_people_source, public_item)
        holding_verified = False
        if expected_holding_label:
            public_label = public_item.get("labels", {}).get("en", {}).get("value") or ""
            holding_verified = normalize("NFC", public_label) == expected_holding_label
        holding_evidence = {}
        if full_source_coverage and not holding_verified:
            from app.pipeline.wikidata_gila_holdings_contents import reviewed_case, reviewed_corrections

            if reviewed_corrections().get(cn, {}).get("property") == "P217":
                holding = current_holding(cn, full_people_source)
                holding_evidence = reviewed_case(
                    cn, full_people_source, public_item, live_entities or {}, holding=holding,
                )
                if holding_evidence.get("verified") and holding:
                    holding_verified = normalize("NFC", public_item.get("labels", {}).get("en", {}).get("value", "")) == holding[2]
        if holding_review:
            case(
                1,
                "identity_or_scope_uncertain",
                "942",
                "holding_associations_require_original_occurrences",
                verified=holding_verified,
            )
        shelfmark = _text(record.get("holding_942_shelfmark"))
        if shelfmark:
            if shelfmark not in {_text(s.get("value")) for s in _claims(item, "P217")}:
                case(1, "present_not_transferred", "942$z", "current_shelfmark_differs")
            expected_label = manuscript_en_label(
                shelfmark,
                _text(record.get("holding_942_name")),
                record,
            )
            labels = item.get("labels") or {}
            if not holding_review and (
                not isinstance(labels, Mapping) or labels.get("en") != expected_label
            ):
                case(1, "present_not_transferred", "942", "structured_english_label_differs")
        else:
            if _text(raw.get("942$z")):
                case(1, "identity_or_scope_uncertain", "942$z", "current_holding_requires_review",
                     verified=holding_verified)
            else:
                case(1, "absent_in_source", "942$z", "no_current_942_shelfmark_in_source",
                     verified=holding_verified)

        if expected_holding_label and not holding_verified:
            for holding_case in cases:
                if holding_case["reason"] in {
                    "holding_associations_require_original_occurrences", "current_holding_requires_review",
                    "no_current_942_shelfmark_in_source",
                }:
                    holding_case["holding_label"] = expected_holding_label
                    holding_case["live_revision"] = public_item.get("lastrevid")
        if repair := holding_evidence.get("source_repair"):
            for holding_case in cases:
                if holding_case["point"] == 1:
                    holding_case["source_repair"] = repair
                    holding_case["source_value"] = holding_evidence["source_value"]

        if not record.get("extent") and record.get("907$n"):
            case(2, "identity_or_scope_uncertain", "907$n", "image_name_does_not_prove_form")
        if not any(
            s.get("value") == "Q118384267" and _qualified(s, "P3831", "Q60474998")
            for s in _claims(item, "P361")
        ):
            case(4, "present_not_transferred", "001", "qualified_ktiv_membership_absent")

        if full_people_source is not None:
            cases.extend(people_role_cases(
                full_people_source, public_item, live_entities,
                authority_rows=(repair_authority_by_cn or {}).get(cn, ()),
                viaf_sources=repair_viaf_sources, gnd_sources=repair_gnd_sources,
                lc_headings=repair_lc_headings,
                lc_death_years=repair_lc_death_years,
                mazal_headings=repair_mazal_headings,
                mazal_people=repair_mazal_people, catalogue_records=repair_catalogue_records,
                linked_bibs=repair_linked_bibs, linked_frames=repair_linked_frames, linked_viaf=repair_linked_viaf,
            ))
        else:
            if people_marc is not None:
                case(5, "absent_in_source", "001", "full_marc_unavailable")
            scribes = [
                row
                for row in _rows(record.get("contributors"))
                if str(row.get("role") or "").casefold() in {"scribe", "transcriber"}
            ]
            projected_scribes = _claims(item, "P11603")
            for scribe in scribes:
                name = _text(scribe.get("name"))
                named = [
                    s
                    for s in projected_scribes
                    if any(
                        (q.get("property") or q.get("property_id")) == "P1932"
                        and _text(q.get("value")) == name
                        for q in _rows(s.get("qualifiers"))
                    )
                ]
                reason = (
                    "named_scribe_identity_unresolved" if any(
                        s.get("value_type") == "somevalue" for s in named
                    ) else "source_scribe_claim_absent" if not named and not projected_scribes
                    else "verify_scribe_target_identity" if not named else ""
                )
                if reason:
                    case(
                        5, "present_not_transferred" if reason == "source_scribe_claim_absent"
                        else "identity_or_scope_uncertain", "700", reason,
                        verified=live_source_match(
                            raw, public_item, live_entities, cn, scribe=True, name=name,
                            authority_rows=(repair_authority_by_cn or {}).get(cn, ()),
                            viaf_sources=repair_viaf_sources,
                            gnd_sources=repair_gnd_sources,
                        ),
                    )
            for work in works_by_cn.get(cn, []):
                if (
                    any(
                        _work_title_key(s.get("value")) == _work_title_key(record.get("title"))
                        for s in _claims(work, "P1476")
                    )
                    and _claims(work, "P2093")
                    and not _claims(work, "P50")
                ):
                    case(5, "identity_or_scope_uncertain", "100", "author_identity_not_linked")

        source_authors[cn] = [_text(row.get("name")) for row in _rows(record.get("authors"))]

        evidence = _rows(item.get("work_candidate_evidence"))
        for candidate in evidence:
            reviewed_matches = [
                row
                for row in reviewed_choices
                if row["target_identity_current"]
                and row["proof"]["candidate"]["kind"] == "work_occurrence"
                and _work_title_key(row["proof"]["candidate"]["title"])
                == _work_title_key(candidate.get("raw_title") or candidate.get("title"))
                and str(row["proof"]["candidate"].get("folio") or "")
                == str(candidate.get("folio_range") or "")
                and row["proof"]["candidate"]["source_field"]
                == str(candidate.get("source_field") or "")
            ]
            if len(reviewed_matches) == 1:
                continue
            if candidate.get("accepted") is not True:
                case(
                    6,
                    "identity_or_scope_uncertain",
                    str(candidate.get("source_field") or ""),
                    str(candidate.get("reason") or "work_candidate_requires_review"),
                )
                if full_source_coverage and str(candidate.get("source_field", "")).casefold() in {"related_works", "730"}:
                    title = _work_title_key(candidate.get("raw_title") or candidate.get("title"))
                    matches = [
                        row for row in marc.occurrences(full_people_source or {}, "730")
                        if title in {_work_title_key(value) for value in marc.values(row, "a")}
                    ]
                    if matches:
                        cases[-1].update(
                            reason="uniform_title_requires_component_proof", source_field="730", marc_730=matches,
                        )
            elif candidate.get("identity_scope") == "record":
                case(
                    3,
                    "identity_or_scope_uncertain",
                    str(candidate.get("source_field") or ""),
                    "descriptive_work_identity_is_source_scoped",
                )

        if record.get("place") and not _claims(item, "P1071"):
            case(
                7, "identity_or_scope_uncertain", "751/260", "production_place_not_resolved",
                verified=live_source_match(raw, public_item, live_entities, cn, scribe=False),
            )
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

        citation_reconciliations = []
        if full_source_coverage:
            for source_row in full_source_cases(
                cn, full_people_source, record, public_item, live_entities or {}, reviewed_catalogs,
                citation_reconciliations=citation_reconciliations,
            ):
                cases.append(source_row)
                totals[source_row["classification"]] += 1
            qid = (qids_by_cn or {}).get(cn)
            if qid and (colophon := source_colophon(full_people_source)):
                colophons_by_qid[qid] = colophon
        else:
            colophon = _text(record.get("colophon_text"))
            if colophon and not is_catalog_note_placeholder(colophon):
                qid = (qids_by_cn or {}).get(cn)
                if qid:
                    colophons_by_qid[qid] = str(record.get("colophon_text") or "").strip()
                transcriptions = [
                    s for s in _claims(item, "P1684") if _qualified(s, "P3831", "Q372474")
                ]
                if not any(colophon in _text(s.get("value")) for s in transcriptions):
                    case(8, "present_not_transferred", "957/590", "full_qualified_colophon_absent")
            elif colophon:
                case(8, "identity_or_scope_uncertain", "957/590", "catalog_prose_requires_review")
            for reference in _rows(record.get("catalog_references")):
                catalog = _text(catalog_reference_code(reference))
                reviewed = reviewed_catalogs.get(
                    (
                        str(reference.get("source_field") or "581"),
                        catalog_reference_text(reference).strip(),
                    )
                )
                if not catalog and reviewed and reviewed["target_identity_current"]:
                    continue
                if catalog and catalog not in {_text(s.get("value")) for s in _claims(item, "P528")}:
                    case(8, "present_not_transferred", "581/942", "catalog_reference_absent")
                elif not catalog and catalog_reference_text(reference):
                    case(
                        8,
                        "identity_or_scope_uncertain",
                        "581/942",
                        "bibliographic_source_identity_unresolved",
                    )

        rows.append(
            {
                "control_number": cn,
                "qid": (qids_by_cn or {}).get(cn),
                "local_id": item.get("local_id"),
                "status": "local_proposal",
                "record_status_notes": list(record.get("record_status_notes") or []),
                "source_extent_text": source_extent,
                "catalog_references": [
                    dict(reference) for reference in _rows(record.get("catalog_references"))
                ],
                "citation_reconciliations": citation_reconciliations,
                "cases": cases,
                "full_people_source": full_people_source is not None,
                "source_passages": source_passages(raw),
                "source_review_choices": reviewed_choices,
            }
        )

    repair_proposals: list[dict[str, object]] = []
    for qid, raw_entity in (live_entities or {}).items() if include_repair_payloads else ():
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
            claim_rows.append(
                {
                    "claim_id": claim.get("id"),
                    "value": datavalue.get("value"),
                    "source_control_numbers": controls,
                }
            )
        controls = sorted(
            {
                cn
                for claim in claim_rows
                if isinstance(claim["source_control_numbers"], list)
                for cn in claim["source_control_numbers"]
            }
        )
        repair_proposals.append(
            {
                "qid": qid,
                "revision": raw_entity.get("lastrevid"),
                "action": "review_source_partition",
                "claims": claim_rows,
                "source_authors": {cn: source_authors.get(cn, []) for cn in controls},
                "entity_sha256": entity_digest(raw_entity),
                "source_partition_claims": [
                    {
                        "property": pid,
                        "claim_id": claim.get("id"),
                        "before": dict(claim),
                        "source_control_numbers": sorted(
                            reference_control_numbers(claim.get("references"))
                        ),
                    }
                    for pid, claim_list in claims.items()
                    for claim in _rows(claim_list)
                ],
                "requires_review": True,
                "requires_authorization": True,
                "requires_fresh_read": True,
                "executable": False,
            }
        )
    fact_comparison = compare_gila_source_facts(
        sources,
        materialized,
        qids_by_cn=qids_by_cn,
        live_entities=live_entities,
        approved_author_qids=approved_author_qids,
        include_claim_payloads=include_repair_payloads,
        full_source_controls=frozenset(people_marc or {}) if full_source_coverage else frozenset(),
    )
    if full_source_coverage:
        from app.pipeline.wikidata_gila_holdings_contents import reviewed_case, reviewed_corrections

        for fact in fact_comparison["facts"]:
            cn = fact["control_number"]
            if reviewed_corrections().get(cn, {}).get("fact_id") != fact["fact_id"]:
                continue
            evidence = reviewed_case(
                cn, (people_marc or {}).get(cn), (live_entities or {}).get(fact.get("qid")),
                live_entities or {}, fact_id=fact["fact_id"],
            )
            if evidence.get("verified"):
                counts = fact_comparison["counts"]
                counts[f"live_{fact['live_status']}"] -= 1
                counts["live_present"] = counts.get("live_present", 0) + 1
                fact.update(
                    live_status="present", live_provenance="source_bound", live_reason="verified_source_assertion",
                    expected_value=evidence["expected_value"], verified_matches=evidence["verified_matches"],
                )
            elif evidence.get("source_repair"):
                fact["source_repair"] = evidence["source_repair"]
    return {
        "mode": "read_only_local_proposal",
        "live_verified": False,
        "model_decision": "separate_physical_manuscripts_and_written_works",
        "record_count": len(rows),
        "full_source_coverage": full_source_coverage,
        "review_classes": list(_REVIEW_CLASSES),
        "case_counts": {category: totals[category] for category in _REVIEW_CLASSES},
        "records": rows,
        "checked_qids": sorted({str(row["qid"]) for row in rows if row["qid"]}),
        "corrected_qids": [],
        "work_repair_proposals": repair_proposals,
        "fact_comparison": fact_comparison,
        "historical_repair_proposals": build_gila_repair_proposals(
            live_entities or {},
            colophons_by_qid=colophons_by_qid,
            include_payloads=include_repair_payloads,
        ),
        "completion_status": "incomplete_requires_review_or_live_receipts",
        "remaining_checks": [
            "verify_dates_places_languages_and_physical_scope",
            "confirm_work_identities_and_authority_links",
            "review_unqualified_or_truncated_transcriptions",
            "inspect_per_item_upload_outcomes_and_live_reference_blocks",
            "read_back_live_claims_before_marking_qids_corrected",
        ],
    }
