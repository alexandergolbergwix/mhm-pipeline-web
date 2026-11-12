"""Compare source facts with final native payloads and live statements."""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from wikibaseintegrator.wbi_config import config as wikibase_config

from app.pipeline.marc_ingest import prepare_record_for_pipeline
from app.pipeline.marc_verify_context import canonical_control_number
from converter.wikidata.catalog_notes import is_catalog_note_placeholder
from converter.wikidata.catalog_references import catalog_reference_code, catalog_reference_text
from converter.wikidata.item_models import snak_identity
from converter.wikidata.property_mapping import (
    LANG_TO_QID,
    SCRIPT_MODE_TO_QID,
    SCRIPT_TYPE_TO_QID,
    date_to_wikidata,
)
from converter.wikidata.work_identity import reference_control_numbers


def _text(value: object) -> str:
    return " ".join(str(value or "").replace('""', '"').split()).strip().rstrip(" ,.;:/-")


def _objects(value: object) -> list[Mapping[str, object]]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
        raise ValueError("Source facts and claims must contain objects.")
    return list(value)


@dataclass(frozen=True)
class SourceFact:
    source_field: str
    property_id: str
    value: str | None
    qualifiers: Mapping[str, str] = field(default_factory=dict)
    source_value: str = ""
    scope: str = "manuscript"
    evidence_only: bool = False
    precision: int | None = None
    calendar: str = ""
    unit: str = ""
    forbids_circa: bool = False
    author_name: str = ""
    source_scoped: bool = False
    candidate_title: str = ""


def _source_title_and_author(entry: Mapping[str, object]) -> tuple[str, str]:
    title = _text(entry.get("source_title") or entry.get("title"))
    author = next(
        (
            _text(entry[key])
            for key in (
                "author",
                "author_name",
                "work_author",
                "responsibility",
                "statement_of_responsibility",
            )
            if entry.get(key)
        ),
        "",
    )
    parts = re.split(
        r"\s+/\s+|\s+[—–-]\s+(?:by|מאת)\s+|\s+(?:חברו|חיברו|חיברוהו|מחברו)\s+",
        title,
        maxsplit=1,
        flags=re.IGNORECASE,
    )
    if len(parts) == 2:
        title = parts[0]
        author = author or re.sub(r"^(?:by|מאת)\s+", "", parts[1], flags=re.IGNORECASE)
    title = title.replace('""', '"')
    if len(title) > 1 and title[0] == title[-1] and title[0] in {'"', "'", "״"}:
        title = title[1:-1].strip()
    return title, author


def _source_facts(record: Mapping[str, object]) -> list[SourceFact]:
    cn = canonical_control_number(record.get("_control_number"))
    facts = [
        SourceFact("001", "P3959", cn, source_value=cn),
        SourceFact(
            "100/245/942",
            "description:en",
            None,
            source_value=str(record.get("title") or ""),
            scope="identity_description",
        ),
    ]
    shelfmark = _text(record.get("holding_942_shelfmark"))
    if shelfmark:
        facts.append(SourceFact("942", "P217", shelfmark, source_value=shelfmark))
    facts.append(SourceFact("001", "P361", "Q118384267", {"P3831": "Q60474998"}, source_value=cn))
    dates = record.get("dates")
    if isinstance(dates, dict) and (date := date_to_wikidata(dates)):
        value, precision, calendar, _earliest, _latest = date
        original = str(dates.get("original_string") or "")
        qualifiers = {}
        stated = (
            str(dates.get("hebrew_full_date") or dates.get("hebrew_date") or original)
            .strip()
            .rstrip(".")
        )
        if any("\u0590" <= character <= "\u05ff" for character in stated):
            qualifiers["P1932"] = stated
        approximate = bool(
            dates.get("approximate")
            or any(
                marker in original.lower()
                for marker in ("ca.", "circa", "בערך", "לערך", "בקירוב", "?", "[")
            )
        )
        date_review = bool(
            re.search(r"מאה|centur", original, re.IGNORECASE)
            and re.search(r"\b[12][0-9]{3}\b", original)
        )
        facts.append(
            SourceFact(
                "260/264$c",
                "P571",
                None if date_review else value,
                qualifiers,
                source_value=original,
                precision=None if date_review else precision,
                calendar="" if date_review else calendar,
                forbids_circa=not date_review and not approximate and precision == 9,
                evidence_only=date_review,
                scope="source_date_conflict_or_boundary_requires_review"
                if date_review
                else "manuscript",
            )
        )
    for language in record.get("languages") or []:
        code = (
            str(language.get("code") or language.get("language") or "")
            if isinstance(language, Mapping)
            else str(language)
        )
        qid = LANG_TO_QID.get(code.lower())
        if qid:
            facts.append(SourceFact("041", "P407", qid, source_value=code))
    extent = record.get("extent")
    if isinstance(extent, (int, float)) and not isinstance(extent, bool) and extent > 0:
        unit = "Q1069725" if record.get("extent_unit") == "page" else "Q107256474"
        facts.append(
            SourceFact(
                "300$a",
                "P1104",
                str(extent),
                source_value=str(record.get("extent_text") or extent),
                unit=unit,
            )
        )
    elif record.get("extent_text"):
        facts.append(
            SourceFact(
                "300$a",
                None,
                source_value=str(record["extent_text"]),
                scope="physical_extent",
                evidence_only=True,
            )
        )
    for kind, table in (("type", SCRIPT_TYPE_TO_QID), ("mode", SCRIPT_MODE_TO_QID)):
        qid = table.get(str(record.get(f"script_{kind}") or ""))
        wording = _text(record.get(f"script_{kind}_source"))
        if qid:
            facts.append(
                SourceFact(
                    "958$a" if kind == "type" else "958$b",
                    "P9302",
                    qid,
                    {"P1932": wording} if wording else {},
                    source_value=wording,
                )
            )
    contents = list(_objects(record.get("contents")))
    authors = {
        _text(row.get("name"))
        for row in _objects(record.get("authors"))
        if row.get("name")
        and str(row.get("role") or "author").casefold()
        in {"author", "presumed author", "attributed author"}
    }
    main_title = _text(record.get("title"))
    if (
        main_title
        and len(authors) == 1
        and not any(
            _text(entry.get("title")) == main_title
            and _text(entry.get("author") or entry.get("author_name") or entry.get("work_author"))
            == next(iter(authors))
            for entry in contents
        )
    ):
        contents.append(
            {"title": main_title, "author": next(iter(authors)), "source_field": "100/245"}
        )
    for entry in contents:
        title, source_author = _source_title_and_author(entry)
        if not title:
            continue
        qualifiers = {"P1932": title}
        folio = _text(entry.get("folio_range"))
        if folio:
            qualifiers["P958"] = folio
        candidate_kind = str(entry.get("candidate_kind") or "")
        source_field = str(entry.get("source_field") or "505")
        review_only = candidate_kind in {
            "catalogue_prose",
            "bibliographic_fragment",
            "descriptive_note",
        }
        facts.append(
            SourceFact(
                source_field,
                "P1574",
                next(
                    (
                        str(entry[key])
                        for key in ("wikidata_id", "wikidata_qid", "existing_qid")
                        if re.fullmatch(r"Q[1-9][0-9]*", str(entry.get(key) or ""))
                    ),
                    None,
                ),
                qualifiers,
                source_value=str(entry.get("source_text") or title),
                scope="content_occurrence",
                evidence_only=review_only,
                author_name=source_author,
                source_scoped=entry.get("identity_scope") == "record",
                candidate_title=_text(entry.get("title")),
            )
        )
    for evidence in _objects(record.get("non_work_content_evidence")):
        facts.append(
            SourceFact(
                "500",
                "",
                None,
                source_value=str(evidence.get("source_text") or ""),
                scope=str(evidence.get("reason") or "non_work_content"),
                evidence_only=True,
            )
        )
    colophon = _text(record.get("colophon_text"))
    if colophon and not is_catalog_note_placeholder(colophon):
        qualifiers = {"P3831": "Q372474"}
        folio = _text(record.get("colophon_folio"))
        if folio:
            qualifiers["P7416"] = folio
        facts.append(SourceFact("957/590", "P1684", colophon, qualifiers, source_value=colophon))
    for reference in _objects(record.get("catalog_references")):
        code = catalog_reference_code(reference)
        facts.append(
            SourceFact(
                str(reference.get("source_field") or "510/942"),
                "P528" if code else "",
                code or None,
                source_value=catalog_reference_text(reference),
                scope="catalogue_code" if code else "bibliographic_source",
                evidence_only=not bool(code),
            )
        )
    for note in record.get("record_status_notes") or []:
        facts.append(
            SourceFact(
                "590",
                "",
                None,
                source_value=str(note),
                scope="catalogue_status",
                evidence_only=True,
            )
        )
    for place in record.get("related_places") or []:
        if not isinstance(place, Mapping) or str(place.get("role") or "").casefold() not in {
            "place of attestation",
            "attestation place",
        }:
            facts.append(
                SourceFact(
                    "751", "", None, source_value=str(place), scope="place_role", evidence_only=True
                )
            )
    return facts


def _snak_value(snak: object) -> str:
    if not isinstance(snak, Mapping):
        raise ValueError("A live snak must be an object.")
    if snak.get("snaktype") in {"somevalue", "novalue"}:
        return str(snak["snaktype"])
    data = snak.get("datavalue")
    if not isinstance(data, Mapping):
        return ""
    value = data.get("value")
    if isinstance(value, Mapping):
        for key in ("id", "text", "time", "amount"):
            if key in value:
                return str(value[key])
        return ""
    return str(value or "")


def _live_claims(entity: Mapping[str, object], pid: str) -> list[Mapping[str, object]]:
    claims = entity.get("claims") or {}
    if not isinstance(claims, Mapping):
        raise ValueError("Live claims must be an object.")
    normalized = []
    for claim in _objects(claims.get(pid)):
        if claim.get("rank") == "deprecated":
            continue
        qualifiers = claim.get("qualifiers") or {}
        if not isinstance(qualifiers, Mapping):
            raise ValueError("Live qualifiers must be an object.")
        normalized.append(
            {
                "claim_id": claim.get("id"),
                "value": _snak_value(claim.get("mainsnak")),
                "qualifiers": [
                    {"property": qpid, "value": _snak_value(snak)}
                    for qpid, rows in qualifiers.items()
                    for snak in _objects(rows)
                ],
                "references": claim.get("references") or [],
            }
        )
        snak = claim.get("mainsnak")
        data = snak.get("datavalue") if isinstance(snak, Mapping) else None
        value = data.get("value") if isinstance(data, Mapping) else None
        if isinstance(value, Mapping):
            normalized[-1].update(
                {key: value[key] for key in ("precision", "calendarmodel", "unit") if key in value}
            )
    return normalized


def _matches(fact: SourceFact, statement: Mapping[str, object]) -> bool:
    if fact.property_id == "description:en":
        description = _text(statement.get("value"))
        return bool(description) and not re.search(r"[\u0590-\u05ff]", description)
    if fact.unit:
        try:
            if Decimal(str(statement.get("value"))) != Decimal(str(fact.value)):
                return False
        except InvalidOperation:
            return False
        if str(statement.get("unit") or "").rsplit("/", 1)[-1] != fact.unit:
            return False
    elif fact.value is not None and _text(statement.get("value")) != _text(fact.value):
        return False
    if fact.precision is not None and statement.get("precision") != fact.precision:
        return False
    if (
        fact.calendar
        and str(statement.get("calendarmodel") or "").rsplit("/", 1)[-1]
        != fact.calendar.rsplit("/", 1)[-1]
    ):
        return False
    qualifiers = _objects(statement.get("qualifiers"))
    if fact.forbids_circa and any(
        (row.get("property") or row.get("property_id")) == "P1480"
        and row.get("value") == "Q5727902"
        for row in qualifiers
    ):
        return False
    return all(
        any(
            (row.get("property") or row.get("property_id")) == pid
            and _text(row.get("value")) == _text(value)
            for row in qualifiers
        )
        for pid, value in fact.qualifiers.items()
    )


def _author_names(statements: Iterable[Mapping[str, object]]) -> set[str]:
    names = set()
    for statement in statements:
        if statement.get("rank") == "deprecated":
            continue
        pid = statement.get("property") or statement.get("property_id")
        if pid == "P2093":
            names.add(_text(statement.get("value")).casefold())
        elif pid == "P50":
            names.update(
                _text(q.get("value")).casefold()
                for q in _objects(statement.get("qualifiers"))
                if (q.get("property") or q.get("property_id")) == "P1932"
            )
    return names - {""}


def _live_work_has_author(
    fact: SourceFact,
    target_qid: str,
    target: Mapping[str, object],
    works: Mapping[str, list[Mapping[str, object]]],
    approved_author_qids: Mapping[tuple[str, str], frozenset[str]],
    control_number: str,
) -> bool:
    claims = [
        {**claim, "property": pid}
        for pid in ("P50", "P2093") for claim in _live_claims(target, pid)
    ]
    name = _text(fact.author_name).casefold()
    native = [
        work for work in works.get(target_qid, [])
        if name in _author_names(_objects(work.get("statements")))
    ]
    native_ids = {
        str(s.get("value"))
        for work in native if len(native) == 1
        for s in _objects(work.get("statements"))
        if (s.get("property") or s.get("property_id")) == "P50"
        and s.get("rank", "normal") != "deprecated"
        and re.fullmatch(r"Q[1-9][0-9]*", str(s.get("value") or ""))
    }
    actual = {str(s.get("value")) for s in _live_claims(target, "P50")}
    if native_ids and actual and actual != native_ids:
        return False
    if name in _author_names(claims):
        return True
    literal_names = _author_names({**c, "property": "P2093"} for c in _live_claims(target, "P2093"))
    if literal_names:
        return False
    expected = approved_author_qids.get((control_number, name), frozenset())
    return len(expected) == 1 and actual == expected and (not native_ids or native_ids == expected)


def _live_work_sources(target: Mapping[str, object]) -> set[str]:
    claims = target.get("claims") or {}
    if not isinstance(claims, Mapping):
        return set()
    return {
        cn for rows in claims.values() for claim in _objects(rows)
        for cn in reference_control_numbers(claim.get("references"))
    } | {str(s.get("value")) for s in _live_claims(target, "P3959")}


def _structured_part_title(title: str) -> bool:
    return bool(re.search(r"(?:^|[\s(])(?:חלק|כרך|פרק|part|volume)\s+\S+", title, re.I))


def _reference_status(
    statements: list[Mapping[str, object]],
    cn: str,
    *,
    live: bool,
) -> str:
    if not statements:
        return "no_matching_claim"
    for statement in statements:
        blocks = statement.get("references" if live else "reference_blocks") or []
        if not blocks:
            return "ambiguous_flat" if statement.get("references") else "missing"
        source_bound = False
        try:
            if not live and statement.get("references"):
                flat = {snak_identity(snak) for snak in _objects(statement["references"])}
                grouped = {snak_identity(snak) for block in blocks for snak in _objects(block)}
                if flat != grouped:
                    return "group_mismatch"
            for block in blocks:
                if live:
                    if not isinstance(block, Mapping) or not isinstance(
                        block.get("snaks"), Mapping
                    ):
                        return "invalid"
                    snaks = [
                        {"property": pid, "value": _snak_value(snak)}
                        for pid, values in block["snaks"].items()
                        for snak in _objects(values)
                        if snak.get("snaktype") == "value"
                    ]
                else:
                    snaks = _objects(block)
                for snak in snaks:
                    pid = snak.get("property") or snak.get("property_id")
                    value = str(snak.get("value") or "")
                    if pid == "P3959" and canonical_control_number(value) == cn:
                        source_bound = True
                    if pid == "P854":
                        host = urlsplit(value).hostname or ""
                        if (
                            host == "nli.org.il" or host.endswith(".nli.org.il")
                        ) and cn in re.findall(r"[0-9]{6,}", value):
                            source_bound = True
        except (TypeError, ValueError):
            return "invalid"
        if not source_bound:
            return "source_mismatch"
    return "source_bound"


def compare_gila_source_facts(
    marc_records: Iterable[Mapping[str, object]],
    items: Iterable[Mapping[str, object]],
    *,
    qids_by_cn: Mapping[str, str] | None = None,
    live_entities: Mapping[str, object] | None = None,
    approved_author_qids: Mapping[tuple[str, str], Iterable[str]] | None = None,
) -> dict[str, object]:
    """Export differences; a read-only comparison never certifies a repair."""
    manuscripts: dict[str, Mapping[str, object]] = {}
    works: dict[str, list[Mapping[str, object]]] = {}
    author_qids = {
        (canonical_control_number(cn), _text(name).casefold()): frozenset(qids)
        for (cn, name), qids in (approved_author_qids or {}).items()
    }
    for item in items:
        if item.get("entity_type") == "work":
            for key in (str(item.get("existing_qid") or ""), f"__LOCAL:{item.get('local_id')}"):
                if key:
                    works.setdefault(key, []).append(item)
        if item.get("entity_type") != "manuscript":
            continue
        for cn in item.get("records") or []:
            canonical_cn = canonical_control_number(cn)
            if canonical_cn in manuscripts:
                raise ValueError(f"Multiple final manuscripts have source {canonical_cn}.")
            manuscripts[canonical_cn] = item
    rows = []
    counts: Counter[str] = Counter()
    for raw in marc_records:
        record = prepare_record_for_pipeline(dict(raw))
        cn = canonical_control_number(record.get("_control_number"))
        if not cn:
            raise ValueError("An audit source needs a control number.")
        item = manuscripts.get(cn, {})
        statements = _objects(item.get("statements"))
        qid = (qids_by_cn or {}).get(cn, "")
        entity = (live_entities or {}).get(qid, {})
        if not isinstance(entity, Mapping):
            raise ValueError("A live entity must be an object.")
        for index, fact in enumerate(_source_facts(record)):
            candidates = [
                row
                for row in statements
                if (row.get("property") or row.get("property_id")) == fact.property_id
            ]
            candidates = [
                {"calendarmodel": wikibase_config["CALENDAR_MODEL_QID"], **row}
                if row.get("value_type") == "time"
                else row
                for row in candidates
            ]
            live_candidates = _live_claims(entity, fact.property_id) if fact.property_id else []
            if fact.property_id == "description:en":
                descriptions = item.get("descriptions") or {}
                candidates = [{"value": descriptions.get("en")}]
                live_descriptions = entity.get("descriptions") or {}
                live_description = live_descriptions.get("en") or {}
                live_candidates = [{"value": live_description.get("value")}]
            proposed = [row for row in candidates if _matches(fact, row)]
            live = [row for row in live_candidates if _matches(fact, row)]
            proposal_status = "present" if proposed else "missing"
            live_status = "present" if live else "missing"
            classification = "present_not_transferred" if not proposed else ""
            reason = (
                "source_fact_absent_from_final_payload" if not proposed else "source_fact_preserved"
            )
            if fact.evidence_only:
                proposal_status = "source_evidence"
                live_status = "source_evidence"
                classification = "identity_or_scope_uncertain"
                reason = (
                    "catalogue_status_evidence"
                    if fact.scope == "catalogue_status"
                    else "source_identity_or_scope_requires_review"
                )
                if fact.scope == "source_date_conflict_or_boundary_requires_review":
                    reason = fact.scope
            elif (
                fact.property_id == "P1574"
                and proposed
                and all(
                    str(row.get("value") or "").startswith("__LOCAL:")
                    or row.get("value") == "Q234460"
                    for row in proposed
                )
            ):
                proposal_status = "local_target_pending"
                classification = "identity_or_scope_uncertain"
                reason = "public_target_requires_resolution"
            elif fact.property_id == "P1574" and fact.value is None:
                classification = "identity_or_scope_uncertain"
                reason = "work_target_identity_requires_review"
                if proposed:
                    proposal_status = "identity_review"
                if live:
                    live_status = "identity_review"
            if fact.property_id == "P1574" and not proposed:
                source_decisions = [
                    evidence
                    for evidence in _objects(item.get("work_candidate_evidence"))
                    if _text(evidence.get("raw_title")).casefold()
                    == fact.candidate_title.casefold()
                    and _text(evidence.get("folio_range")) == fact.qualifiers.get("P958", "")
                    and (
                        evidence.get("accepted") is False
                        or evidence.get("projection_status") == "deferred"
                    )
                ]
                if source_decisions:
                    proposal_status = "deferred"
                    classification = "identity_or_scope_uncertain"
                    reason = ";".join(
                        sorted(
                            {
                                str(evidence.get("projection_reason") or evidence.get("reason"))
                                for evidence in source_decisions
                            }
                        )
                    )
                    if not live:
                        note_reasons = {
                            "catalogue_prose", "descriptive_note", "person_name_not_work",
                            "folio_range_title", "generic_manuscript_title",
                        }
                        reasons = {
                            str(e.get("projection_reason") or e.get("reason"))
                            for e in source_decisions
                        }
                        is_note = reasons <= note_reasons or (
                            reasons == {"bibliographic_fragment"}
                            and not _structured_part_title(fact.candidate_title)
                        )
                        live_status = "source_evidence" if is_note else "identity_review"
            if fact.property_id == "P1574" and (fact.author_name or fact.source_scoped):
                proposed_compatible = []
                for candidate in proposed:
                    targets = works.get(str(candidate.get("value") or ""), [])
                    compatible = len(targets) == 1
                    if compatible and fact.author_name:
                        compatible = _text(fact.author_name).casefold() in _author_names(
                            _objects(targets[0].get("statements"))
                        )
                    if compatible and fact.source_scoped:
                        compatible = {
                            canonical_control_number(value)
                            for value in targets[0].get("records") or []
                        } == {cn}
                    proposed_compatible.append(compatible)
                if proposed:
                    one_proposed = len({str(c.get("value")) for c in proposed}) == 1
                    if not all(proposed_compatible) or not one_proposed:
                        proposal_status = "identity_review"
                        classification = "identity_or_scope_uncertain"
                        reason = "work_author_or_source_requires_review"
                    elif proposal_status == "identity_review":
                        proposal_status = "present"
                live_compatible = []
                for candidate in live:
                    target_qid = str(candidate.get("value") or "")
                    target = (live_entities or {}).get(target_qid)
                    compatible = isinstance(target, Mapping)
                    if compatible and isinstance(target, Mapping) and fact.author_name:
                        compatible = _live_work_has_author(
                            fact, target_qid, target, works, author_qids, cn,
                        )
                    if compatible and isinstance(target, Mapping) and fact.source_scoped:
                        compatible = _live_work_sources(target) == {cn}
                    live_compatible.append(compatible)
                if live:
                    one_target = len({str(c.get("value")) for c in live}) == 1
                    live_status = (
                        "present" if all(live_compatible) and one_target else "identity_review"
                    )
            live_target_missing = live_entities is not None and (
                not qid or qid not in live_entities
            )
            if (live_entities is None or live_target_missing) and not fact.evidence_only:
                live_status = "not_checked"
            provenance_applies = bool(fact.property_id) and fact.property_id != "description:en"
            proposal_provenance = (
                _reference_status(proposed, cn, live=False)
                if provenance_applies
                else "not_applicable"
            )
            live_provenance = (
                (_reference_status(live, cn, live=True) if provenance_applies else "not_applicable")
                if live_entities is not None and not live_target_missing
                else "not_checked"
            )
            provenance_review = any(
                status
                in {"missing", "ambiguous_flat", "invalid", "source_mismatch", "group_mismatch"}
                for status in (proposal_provenance, live_provenance)
            )
            proposal_reason = reason
            live_classification = ""
            live_reason = "not_checked" if live_status == "not_checked" else "live_fact_preserved"
            if live_target_missing:
                live_classification = "identity_or_scope_uncertain"
                live_reason = (
                    "manuscript_qid_not_resolved" if not qid else "entity_absent_from_live_snapshot"
                )
            if live_status == "missing":
                if fact.property_id == "P1574" and fact.value is None:
                    live_classification = "identity_or_scope_uncertain"
                    live_reason = "live_work_identity_or_scope_requires_review"
                else:
                    live_classification = "historical_live_defect"
                    live_reason = "source_fact_absent_from_live_entity"
            elif live_status == "identity_review":
                live_classification = "identity_or_scope_uncertain"
                live_reason = "live_work_author_or_source_requires_review"
            elif live_status == "source_evidence":
                live_classification = "identity_or_scope_uncertain"
                live_reason = reason
            if live_classification and proposal_status == "present":
                reason = live_reason
            counts[f"proposal_{proposal_status}"] += 1
            counts[f"live_{live_status}"] += 1
            identity = json.dumps(
                [cn, index, fact.source_field, fact.property_id, fact.value, dict(fact.qualifiers)],
                ensure_ascii=False,
            )
            rows.append(
                {
                    "fact_id": hashlib.sha256(identity.encode()).hexdigest(),
                    "control_number": cn,
                    "qid": qid,
                    "source_field": fact.source_field,
                    "scope": fact.scope,
                    "source_value": fact.source_value,
                    "property": fact.property_id,
                    "expected_value": fact.value,
                    "expected_qualifiers": dict(fact.qualifiers),
                    "expected_precision": fact.precision,
                    "expected_calendar": fact.calendar,
                    "expected_unit": fact.unit,
                    "expected_author": fact.author_name,
                    "source_scoped": fact.source_scoped,
                    "proposal_status": proposal_status,
                    "live_status": live_status,
                    "classification": classification,
                    "reason": reason,
                    "proposal_reason": proposal_reason,
                    "live_classification": live_classification,
                    "live_reason": live_reason,
                    "proposal_provenance": proposal_provenance,
                    "live_provenance": live_provenance,
                    "provenance_reason": "source_references_require_review"
                    if provenance_review
                    else "",
                    "proposed_claims": proposed,
                    "live_claims": live,
                    "observed_live_claims": live_candidates if not live else [],
                    "live_revision": entity.get("lastrevid"),
                    "review_status": "pending"
                    if classification or live_classification or provenance_review
                    else "not_required",
                }
            )
    return {
        "facts": rows,
        "counts": dict(counts),
        "corrected_qids": [],
        "basis": "prepared_source_to_final_native_and_live",
    }
