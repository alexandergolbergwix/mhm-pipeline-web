"""Bind contents repairs and passage coverage to the original MARC evidence."""

import re
from collections import defaultdict
from collections.abc import Mapping
from unicodedata import normalize

from app.pipeline import marc_occurrences as marc
from app.pipeline.wikidata_gila_facts import _live_claims, _reference_status, _snak_value, _text
from app.pipeline.wikidata_gila_work_evidence import exact_occurrence, same_occurrence, work_target
from app.pipeline.wikidata_source_choices import _claim_values, candidates
from converter.wikidata.work_candidates import assess_work_candidate


def title_key(value):
    return _text(normalize("NFC", str(value or "")).translate(str.maketrans('״“”', '"""'))).casefold()


def heading_key(value):
    return title_key(str(value or "").replace(",", " "))


def original_work_occurrences(record):
    fields = marc.occurrences(record)
    tags = {field["tag"] for field in fields}
    if not tags.intersection({"245", "500", "505", "740"}):
        return {}
    headings = [field for field in fields if field["tag"] == "100"]
    original = defaultdict(list)
    for row in candidates(record):
        if row["kind"] != "work_occurrence" or row["scope_alignment_unproven"]:
            continue
        source = row["source_field"]
        if source == "100/245":
            if not row["author"] or len(headings) != 1 or "245" not in tags:
                continue
            names = marc.values(headings[0], "a")
            if len(names) != 1:
                continue
            heading_variants = {heading_key(names[0]), heading_key(" ".join([
                names[0], *marc.values(headings[0], "c")
            ]))}
            if heading_key(row["author"]) not in heading_variants:
                continue
            original[(source, title_key(row["title"]), "")].append({
                **row, "author_aliases": tuple(sorted(heading_variants)), "author_heading": headings[0],
            })
            continue
        elif source not in {"500", "505", "740"} or not any(
            field["tag"] == source and title_key(row["source_text"]) in {
                title_key(value) for code in ("a", "t") for value in marc.values(field, code)
            }
            for field in fields
        ):
            continue
        key = (source, title_key(row["title"]), _text(row["folio"]), title_key(row["author"]))
        original[key].append(row)
    return original


def _published_work_title(entity, title):
    classes = {row["value"] for row in _live_claims(entity, "P31")}
    if not classes.intersection({"Q47461344", "Q7725634", "Q571"}) or "Q87167" in classes:
        return False
    names = {title_key(row["value"]) for row in _live_claims(entity, "P1476")}
    names.update(title_key(row.get("value")) for row in entity.get("labels", {}).values())
    names.update(title_key(row.get("value")) for rows in entity.get("aliases", {}).values() for row in rows)
    return title in names


def reconcile_work(manuscript, cn, fact, originals, work_qids, entities, author_matches):
    """Use a single original work occurrence and current, compatible public identity."""
    from app.pipeline.wikidata_gila_sources import source_case

    if (
        fact.evidence_only or fact.reviewed_identity_current
        or fact.source_field not in {"100/245", "500", "505", "740"}
        or not manuscript.get("id") or type(manuscript.get("lastrevid")) is not int
        or set(_claim_values(manuscript, "P3959")) != {cn}
    ):
        return {}
    title = title_key(fact.qualifiers.get("P1932"))
    folio = _text(fact.qualifiers.get("P958"))
    source = fact.source_field
    key = (source, title, folio) if source == "100/245" else (source, title, folio, title_key(fact.author_name))
    matching = originals.get(key, ())
    if len(matching) != 1:
        return {}
    occurrence = matching[0]
    if source == "740":
        # A related title on its own does not establish physical contents.
        return {}
    if not assess_work_candidate(
        occurrence["title"], source_field=source, candidate_kind="named_work",
    ).accepted:
        return {}
    # A catalogue contents note must locate the component. A full MARC
    # main title with its named author identifies a work without a folio.
    if source != "100/245" and not folio:
        return {}
    if source == "500":
        lead, _, description = occurrence["source_text"].partition(":")
        if not re.match(r"^(?:ב?דף|ב?דפים|ב?דפי|fols?\.?)\s", lead.strip(), re.I):
            return {}
        anchored_title = title_key(description.lstrip(' "\u05f4'))
        if anchored_title != title and not anchored_title.startswith((title + " ", title + ".")):
            return {}

    live = _live_claims(manuscript, "P1574")
    scoped = [claim for claim in live if same_occurrence(claim, fact.qualifiers)]
    if len(scoped) > 1 or scoped and not exact_occurrence(scoped[0], fact.qualifiers):
        return {}
    target_ids = set(work_qids) | {claim["value"] for claim in scoped}
    if fact.value:
        target_ids &= {fact.value}
    valid = []
    for qid in target_ids - {manuscript["id"]}:
        entity = entities.get(qid)
        if (
            not entity or entity.get("type") != "item" or entity.get("id") != qid
            or any(key in entity for key in ("missing", "redirect", "redirects"))
            or type(entity.get("lastrevid")) is not int or entity["lastrevid"] <= 0
        ):
            continue
        author_confirmed, author_dependencies = author_matches(entity, occurrence)
        source_title = work_target(entity, cn, occurrence["title"])
        if not source_title and not (
            source == "100/245" and author_dependencies and _published_work_title(entity, title)
        ):
            continue
        if fact.author_name and not author_confirmed:
            continue
        valid.append((entity, author_dependencies))
    if len(valid) != 1:
        return {}
    target, author_dependencies = valid[0]
    if scoped and scoped[0]["value"] != target["id"]:
        return {}
    if any(
        _snak_value(row["mainsnak"]) == target["id"]
        and (row.get("rank") == "deprecated" or not scoped or row.get("id") != scoped[0]["claim_id"])
        for row in manuscript.get("claims", {}).get("P1574", ())
    ):
        return {}
    if scoped:
        claim = scoped[0]
        if not claim["claim_id"] or any(
            q["property"] not in {"P1932", "P958"}
            and not (q["property"] == "P1480" and q["value"] == "Q18122778")
            for q in claim["qualifiers"]
        ):
            return {}
        if _reference_status(scoped, cn, live=True) == "source_bound":
            return {
                "verified": True,
                "verified_matches": [{"statement_id": claim["claim_id"], "value": target["id"]}],
            }
        if claim["references"]:
            return {}

    return source_case(
        cn, occurrence["source_key"], occurrence["source_text"], "full_marc_contents",
        entity=manuscript, pid="P1574", value=target["id"], qualifiers=dict(fact.qualifiers),
        kind="item", dependencies=(target, *author_dependencies),
    )


def _full_passage(passage, fields):
    raw = passage.get("source_occurrence")
    tag = passage["source_field"]
    value = title_key(passage.get("source_text"))
    possible = [
        row for row in fields
        if row["tag"] == tag and value in {
            title_key(" ".join(sf["value"] for sf in row["subfields"])),
            title_key(" ".join(marc.values(row, "a"))),
        }
    ]
    if isinstance(raw, Mapping):
        exact = [row for row in possible if row == raw]
        if exact and len(possible) == 1:
            return exact[0]
    return possible[0] if len(possible) == 1 else None


def contents_passage_coverage(passages, checks, records):
    """Report passage coverage independently of the requirement 6 denominator."""
    titles = defaultdict(list)
    for check in checks:
        qualifiers = check.get("expected_qualifiers") or {}
        if check.get("property") == "P1574" and (title := title_key(qualifiers.get("P1932"))):
            titles[(check.get("control_number"), check["qid"], title)].append(check)

    coverage = {tag: {"total": 0, "verified": 0, "title_matches": 0, "with_folios": 0}
                for tag in ("740", "505")}
    fields_by_cn = {}
    originals_by_cn = {}
    for passage in passages:
        tag = passage.get("source_field")
        if tag not in coverage:
            continue
        result = coverage[tag]
        result["total"] += 1
        cn = passage["control_number"]
        record = records.get(cn)
        if not record:
            continue
        if cn not in fields_by_cn:
            fields_by_cn[cn] = marc.occurrences(record)
        field = _full_passage(passage, fields_by_cn[cn])
        if not field:
            continue
        if tag == "505" and any(sf["code"] not in {"a", "t", "6", "7", "8"} for sf in field["subfields"]):
            continue
        if tag == "740" and any(sf["code"] not in {"a", "6", "8", "9"} for sf in field["subfields"]):
            continue
        titles_in_field, prose = marc.values(field, "t"), marc.values(field, "a")
        if titles_in_field and prose:
            continue
        values = titles_in_field or prose
        if not values or any(not title_key(value) for value in values):
            continue
        supports = []
        for value in values:
            matches = titles[(cn, passage.get("qid"), title_key(value))]
            claim_ids = {match["statement_id"] for check in matches for match in check["matches"]
                         if match.get("statement_id")}
            if len(claim_ids) != 1:
                break
            supports.append(matches)
        if len(supports) != len(values):
            continue
        result["title_matches"] += 1
        folios = [
            {_text(check["expected_qualifiers"].get("P958")) for check in matches
             if check["expected_qualifiers"].get("P958")} for matches in supports
        ]
        if any(len(ranges) != 1 for ranges in folios):
            continue
        result["with_folios"] += 1
        if tag == "505" or field["indicators"][1] == "2":
            result["verified"] += 1
            continue
        if cn not in originals_by_cn:
            originals_by_cn[cn] = original_work_occurrences(record)
        if all(any(
            key[0] in {"500", "505"} and key[1] == title_key(value) and key[2] in ranges
            and len(rows) == 1
            for key, rows in originals_by_cn[cn].items()
        ) for value, ranges in zip(values, folios, strict=True)):
            result["verified"] += 1
    return coverage
