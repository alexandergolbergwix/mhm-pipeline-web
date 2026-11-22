"""Prove manuscript identity claims from the catalogue and authority evidence."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict

from app.pipeline import marc_occurrences
from app.pipeline.marc_ingest import prepare_record_for_pipeline
from app.pipeline.wikidata_gila_gnd_evidence import gnd_identifier
from app.pipeline.wikidata_source_choices import _claim_values
from converter.wikidata.item_models import WikidataStatement, merge_statement_references
from converter.wikidata.property_mapping import gnd_reference, nli_reference, viaf_reference

SCRIBE_REASONS = frozenset({
    "named_scribe_identity_unresolved", "source_scribe_claim_absent", "verify_scribe_target_identity",
})
REVIEW_REASONS = SCRIBE_REASONS | {"production_place_not_resolved"}
_QID = r"Q[1-9][0-9]*"
_SCRIBE_TERMS = {"scribe", "transcriber", "מעתיק", "מעתיקה", "scr", "trc"}
_PLACE_ROLES = {"place of writing", "place of origin", "production place", "place of creation",
                "origin", "written"}


def _relators(row):
    return {
        text.strip().strip('()"').casefold()
        for code in ("e", "4") for text in marc_occurrences.values(row, code)
    }


def _source_evidence(raw: dict, *, scribe: bool) -> tuple[list[str], str, str | None]:
    record = prepare_record_for_pipeline(raw.copy())
    tags = ("700", "800") if scribe else ("751", "260", "264")
    roles = _SCRIBE_TERMS if scribe else _PLACE_ROLES
    excerpts = []
    stated_qids = set()
    labels = []
    for tag in tags:
        for row in marc_occurrences.occurrences(record, tag):
            names = marc_occurrences.values(row, "a")
            identifiers = marc_occurrences.values(row, "0") + marc_occurrences.values(row, "1")
            row_roles = _relators(row)
            excerpts.append(
                f"{marc_occurrences.source_key(row)} "
                + " ".join(f"${sf['code']} {sf['value']}" for sf in row["subfields"])
            )
            if len(names) == 1 and row_roles & roles:
                labels.append(names[0])
                for identifier in identifiers:
                    match = re.fullmatch(
                        rf"(?:https://www\.wikidata\.org/(?:entity|wiki)/)?({_QID})",
                        identifier.strip(),
                    )
                    if match:
                        stated_qids.add(match[1])
    if not excerpts:
        excerpts = [
            f"{key} {raw[key]}" for tag in tags for code in ("a", "d", "e", "0", "1")
            if raw.get(key := f"{tag}${code}")
        ]
    exact_occurrences = len(labels)
    if not labels:
        if scribe:
            labels = [
                str(row["name"]) for row in record.get("contributors", [])
                if row.get("name") and str(row.get("role", "")).casefold() in roles
            ]
        elif record.get("place"):
            labels = [str(record["place"])]
    distinct_labels = {label.strip() for label in labels if label.strip()}
    label = next(iter(distinct_labels)) if len(distinct_labels) == 1 else ""
    qid = next(iter(stated_qids)) if label and exact_occurrences == len(stated_qids) == 1 else None
    return [excerpt[:600] for excerpt in excerpts[:10]], label, qid


def _place_parts(raw: dict) -> tuple[str, str, str] | None:
    record = prepare_record_for_pipeline(raw.copy())
    rows = marc_occurrences.occurrences(record, "751")
    if rows:
        if any(len(marc_occurrences.values(row, "a")) != 1
               for row in rows if _relators(row) & _PLACE_ROLES):
            return None
        qualified = [
            marc_occurrences.values(row, "a")[0]
            for row in rows if _relators(row) & _PLACE_ROLES
        ]
    else:
        names = marc_occurrences.split_slots(raw.get("751$a"))
        roles = marc_occurrences.split_slots(raw.get("751$e"))
        if len(names) != len(roles):
            return None
        qualified = [
            name for name, role in zip(names, roles, strict=True)
            if role.strip().strip('"').casefold() in _PLACE_ROLES
        ]
    if len(qualified) != 1:
        return None
    match = re.fullmatch(r"([^()]+)\(([^(),]+)(?:,([^(),]+))?\)", qualified[0].strip())
    if not match:
        return None
    name, qualifier, country = (value.strip() if value else "" for value in match.groups())
    if not name or not qualifier or (match[3] is not None and not country):
        return None
    return (name, qualifier, country) if country else (name, "", qualifier)


def _authority_ids(raw: dict) -> set[tuple[str, str]]:
    record = prepare_record_for_pipeline(raw.copy())
    rows = [
        row for tag in ("700", "800") for row in marc_occurrences.occurrences(record, tag)
        if len(marc_occurrences.values(row, "a")) == 1
        and _SCRIBE_TERMS & _relators(row)
    ]
    if len(rows) != 1:
        return set()
    ids = set()
    for code in ("0", "1"):
        for value in marc_occurrences.values(rows[0], code):
            if match := re.fullmatch(r"https?://(?:www\.)?viaf\.org/viaf/([0-9]+)/?", value.strip()):
                ids.add(("P214", match[1]))
            elif match := re.fullmatch(
                r"(?:https?://www\.nli\.org\.il/(?:en|he)/authorities/|\(NNL\))?(987[0-9]{6,21})",
                value.strip(),
            ):
                ids.add(("P8189", match[1]))
    return ids


def _wikidata_links(entity: dict, property_id: str) -> set[str]:
    return {
        value["id"] for value in _claim_values(entity, property_id)
        if isinstance(value, dict) and re.fullmatch(_QID, str(value.get("id", "")))
    }


def _names(entity: dict) -> set[str]:
    return {
        row["value"] for row in entity.get("labels", {}).values()
    } | {
        row["value"] for rows in entity.get("aliases", {}).values() for row in rows
    }


def _place_key(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())


def _places_agree(entity: dict, context: dict, parts: tuple[str, str, str]) -> bool:
    name, region, jurisdiction = map(_place_key, parts)
    countries = _wikidata_links(entity, "P17")
    parents = _wikidata_links(entity, "P131")
    parents |= {
        parent for qid in parents for parent in _wikidata_links(context.get(qid, {}), "P131")
    }
    parent_names = {_place_key(value) for qid in parents for value in _names(context.get(qid, {}))}
    country_names = {_place_key(value) for qid in countries for value in _names(context.get(qid, {}))}
    containment = (
        region in parent_names and jurisdiction in country_names
        if region else jurisdiction in country_names | parent_names
    )
    return (
        bool(_claim_values(entity, "P625"))
        and name in {_place_key(value) for value in _names(entity)}
        and containment
    )


def _source_scribe_rows(raw):
    record = prepare_record_for_pipeline(raw.copy())
    return [row for row in record.get("contributors", [])
            if row.get("name") and str(row.get("role", "")).casefold() in {"scribe", "transcriber"}]


def _approved_authority_ids(raw, rows) -> set[tuple[str, str]]:
    from converter.authority.evidence import normalize_authority_id, normalize_viaf_id

    scribes = _source_scribe_rows(raw)
    if len(scribes) != 1:
        return set()
    name = " ".join(str(scribes[0]["name"]).casefold().split())
    if "?" in name or "？" in name:
        return set()
    matches = set()
    for row in rows:
        if (
            row.entity_kind != "person" or not row.approved or row.confidence != "high"
            or row.source not in {"mazal", "viaf", "cross_source"}
            or row.role.casefold() not in {"scribe", "transcriber"}
            or " ".join(row.entity_text.casefold().split()) != name
            or (row.payload or {}).get("guard_flags")
        ):
            continue
        viaf = normalize_viaf_id(row.viaf_id)
        nli = normalize_authority_id(row.mazal_id)
        ids = frozenset(
            (pid, value) for pid, value in (("P214", viaf), ("P8189", nli))
            if value and value.isascii() and value.isdigit()
        )
        if ids:
            matches.add(ids)
    return set(next(iter(matches))) if len(matches) == 1 else set()


def _scribe_life_dates(raw):
    scribes = _source_scribe_rows(raw)
    if len(scribes) != 1:
        return None
    row = scribes[0]
    name = row["name"]
    dates = re.fullmatch(r"([12][0-9]{3})\s*[-–]\s*([12][0-9]{3})\.?", str(row.get("dates") or "").strip())
    if not dates or "?" in name or "？" in name or not 0 < int(dates[2]) - int(dates[1]) < 120:
        return None
    return name, dates[1], dates[2]


def _heading_key(value: str) -> str:
    return " ".join(re.findall(r"[^\W\d_]+", value.casefold()))


def _viaf_proves_scribe(raw, identifier, cluster):
    return viaf_proves_person(_scribe_life_dates(raw), identifier, cluster)


def viaf_proves_person(source, identifier, cluster):
    from converter.authority.biodata import extract_viaf_biodata

    if not source or not cluster:
        return False
    name, birth, death = source
    cluster = cluster.get("ns1:VIAFCluster", cluster)
    if (
        str(cluster.get("ns1:viafID") or cluster.get("viafID") or "") != identifier
        or (cluster.get("ns1:nameType") or cluster.get("ns2:nameType") or cluster.get("nameType")) != "Personal"
    ):
        return False
    bio = extract_viaf_biodata(cluster)
    names = [_heading_key(heading) for headings in bio.names.values() for heading in headings]
    return (
        _heading_key(name) in names
        and re.fullmatch(rf"{birth}(?:-[0-9]{{2}}(?:-[0-9]{{2}})?)?", bio.dates.get("birth", "")) is not None
        and re.fullmatch(rf"{death}(?:-[0-9]{{2}}(?:-[0-9]{{2}})?)?", bio.dates.get("death", "")) is not None
    )


def _gnd_heading(value):
    return _heading_key(unicodedata.normalize("NFC", value))


def _gnd_proves_scribe(raw, entity, record):
    source = _scribe_life_dates(raw)
    if not source or not record or record["gndIdentifier"] != gnd_identifier(entity):
        return False
    name, birth, death = source
    if (
        record.get("dateOfBirth") != [birth] or record.get("dateOfDeath") != [death]
        or len(_gnd_heading(name).split()) < 3
    ):
        return False
    labels = {_gnd_heading(value) for value in _names(entity)}
    headings = {
        _gnd_heading(value) for value in [record.get("preferredName"), *record.get("variantName", [])]
        if isinstance(value, str)
    } - {""}
    return _gnd_heading(name) in labels and bool(headings & labels)


def _viaf_identifier(entity):
    values = _claim_values(entity, "P214")
    if len(values) == 1 and isinstance(values[0], str) and re.fullmatch(r"[0-9]{1,20}", values[0]):
        return values[0]
    return None


def _viaf_reference_ids(references):
    return {
        match[1]
        for ref in references
        for snak in ref.get("snaks", {}).get("P854", [])
        if isinstance((value := snak.get("datavalue", {}).get("value")), str)
        and (match := re.fullmatch(r"https://viaf\.org/viaf/([0-9]{1,20})/?", value))
    }


def _viaf_citation_ids(entity, properties=("P11603",)):
    return {
        identifier
        for pid in properties
        for claim in entity.get("claims", {}).get(pid, [])
        if claim.get("rank") != "deprecated"
        for identifier in _viaf_reference_ids(claim.get("references", []))
    }


def _library_of_congress_confirms_scribe(raw, candidate, claim, authority_rows, viaf_sources, lc_headings):
    from app.pipeline.wikidata_gila_authority_reads import library_of_congress_identifier

    if not lc_headings:
        return False
    source = _scribe_life_dates(raw)
    viaf = _viaf_identifier(candidate)
    if (
        not source or not viaf or (viaf_sources or {}).get(viaf)
        or viaf not in _viaf_reference_ids(claim["references"])
        or "Q5" not in _wikidata_links(candidate, "P31")
    ):
        return False
    _, _, stated_qid = _source_evidence(raw, scribe=True)
    authority_ids = _authority_ids(raw) | _approved_authority_ids(raw, authority_rows)
    if (stated_qid and stated_qid != candidate["id"]
        or any(value not in _claim_values(candidate, pid) for pid, value in authority_ids)):
        return False
    name, birth, death = source
    for label in (lc_headings or {}).get(library_of_congress_identifier(candidate), ()):
        match = re.fullmatch(rf"(.+?)[, ]+{birth}\s*[-–־]\s*{death}\.?\s*", label)
        if match and _heading_key(match[1]) == _heading_key(name):
            return True
    return False


def _proven_candidates(
    raw, entities, context, *, scribe, source_qid, parts,
    authority_rows=(), viaf_sources=None, gnd_sources=None,
):
    if scribe:
        authorities = _authority_ids(raw) | _approved_authority_ids(raw, authority_rows)
        return {
            qid for qid, entity in entities.items()
            if (qid == source_qid if source_qid else bool(authorities) or (
                (identifier := _viaf_identifier(entity))
                and _viaf_proves_scribe(raw, identifier, (viaf_sources or {}).get(identifier))
            ) or _gnd_proves_scribe(raw, entity, (gnd_sources or {}).get(gnd_identifier(entity))))
            and all(value in _claim_values(entity, pid) for pid, value in authorities)
        }
    if source_qid:
        return {source_qid} & entities.keys()
    return {
        qid for qid, entity in entities.items()
        if parts and _places_agree(entity, context, parts)
    }


def verified_candidate(
    raw, entity, context, *, scribe, authority_rows=(), viaf_sources=None, gnd_sources=None,
):
    _, _, source_qid = _source_evidence(raw, scribe=scribe)
    qid = entity["id"]
    proven = _proven_candidates(
        raw, {qid: entity}, context, scribe=scribe, source_qid=source_qid,
        parts=None if scribe else _place_parts(raw), authority_rows=authority_rows,
        viaf_sources=viaf_sources, gnd_sources=gnd_sources,
    )
    if not proven:
        return False
    return ("Q5" in _wikidata_links(entity, "P31") if scribe
            else bool(_claim_values(entity, "P625")))


def live_source_match(
    raw, manuscript, entities, cn, *, scribe, name="",
    authority_rows=(), viaf_sources=None, gnd_sources=None, lc_headings=None,
):
    from app.pipeline.wikidata_gila_facts import _live_claims, _reference_status

    if not manuscript or not entities or set(_claim_values(manuscript, "P3959")) != {cn}:
        return False
    pid = "P11603" if scribe else "P1071"
    claims = _live_claims(manuscript, pid)
    if scribe:
        from app.pipeline.wikidata_gila_facts import SourceFact, _matches

        expected = SourceFact("700/800", pid, None, {"P1932": name})
        claims = [row for row in claims if _matches(expected, row) and row["value"] != "somevalue"]
    if len(claims) != 1:
        return False
    candidate = entities.get(claims[0]["value"])
    if not candidate or _reference_status(claims, cn, live=True) != "source_bound":
        return False
    return verified_candidate(
        raw, candidate, entities, scribe=scribe, authority_rows=authority_rows,
        viaf_sources=viaf_sources, gnd_sources=gnd_sources,
    ) or (scribe and _library_of_congress_confirms_scribe(
        raw, candidate, claims[0], authority_rows, viaf_sources, lc_headings,
    ))


def _repair_statement(
    raw, case, manuscript, candidate, context, *,
    authority_rows=(), viaf_sources=None, gnd_sources=None,
):
    """Build an additive statement only from exact catalogue and public evidence."""
    scribe = case["reason"] in SCRIBE_REASONS
    cn = case["control_number"]
    if (
        manuscript.get("id") != case["qid"] or candidate["id"] == case["qid"]
        or set(_claim_values(manuscript, "P3959")) != {cn}
        or not verified_candidate(
            raw, candidate, context, scribe=scribe, authority_rows=authority_rows,
            viaf_sources=viaf_sources, gnd_sources=gnd_sources,
        )
    ):
        return None
    pid = "P11603" if scribe else "P1071"
    qualifiers = []
    claims = [
        claim for claim in manuscript.get("claims", {}).get(pid, [])
        if claim.get("rank") != "deprecated"
    ]
    if scribe:
        _, name, _ = _source_evidence(raw, scribe=True)
        name = name.rstrip(",").strip()
        if not name or "?" in name or "？" in name:
            return None
        qualifiers = [{"property": "P1932", "value": name, "type": "string"}]
        # Retain existing placeholders. Reject any concrete or conflicting claim.
        if any(
            claim.get("mainsnak", {}).get("snaktype") != "somevalue"
            or [snak.get("datavalue", {}).get("value")
                for snak in claim.get("qualifiers", {}).get("P1932", [])] != [name]
            for claim in claims
        ):
            return None
    elif claims:
        return None
    claim = WikidataStatement(
        property_id=pid, value=candidate["id"], value_type="item",
        qualifiers=qualifiers, references=nli_reference(cn),
    )
    if scribe and (identifier := _viaf_identifier(candidate)) and identifier in (viaf_sources or {}):
        merge_statement_references(claim, WikidataStatement(
            pid, candidate["id"], "item", references=viaf_reference(identifier),
        ))
    elif scribe and (identifier := gnd_identifier(candidate)) and identifier in (gnd_sources or {}):
        merge_statement_references(claim, WikidataStatement(
            pid, candidate["id"], "item", references=gnd_reference(identifier),
        ))
    statement = asdict(claim)
    statement["property"] = statement.pop("property_id")
    return statement
