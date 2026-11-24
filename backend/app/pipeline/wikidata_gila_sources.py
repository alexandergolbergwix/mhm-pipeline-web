"""Audit publication, access, inscription, and content evidence by MARC occurrence."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from urllib.parse import urlsplit

from app.pipeline import marc_occurrences as marc
from app.pipeline.marc_ingest import _COLOPHON_LABEL_RE, _TITLE_PAGE_PREFIX_RE, prepare_record_for_pipeline
from app.pipeline.wikidata_gila_facts import _live_claims, _snak_value
from app.pipeline.wikidata_gila_people import _heading, _work_target
from app.pipeline.wikidata_gila_reviewed_sources import reviewed_source_items, reviewed_source_proof
from app.pipeline.wikidata_source_choices import _claim_values
from converter.wikidata.catalog_notes import is_catalog_note_placeholder
from converter.wikidata.catalog_references import catalog_reference_code, catalog_reference_text
from converter.wikidata.content_projection import _work_title_key
from converter.wikidata.marc_subject_resolve import _static_genre_qid, _static_subject_qid, genre_projection_supported

_SCAN_HOSTS = {
    "gallica.bnf.fr", "digi.vatlib.it", "real-ms.mtak.hu", "digital.bodleian.ox.ac.uk",
    "digitalcollections.jtsa.edu", "digitalesamlinger.kb.dk", "mss.huc.edu",
    "www.digitalcollections.manchester.ac.uk", "resolver.sub.uni-hamburg.de",
    "resolver.staatsbibliothek-berlin.de", "daten.digitale-sammlungen.de",
}
_ONLINE = re.compile(r"For online version(?: \((?:color|colour|bnw)\))?, click here\Z", re.I)
_COPY_LIMITS = {"3", "5"}
_TOPIC_SUBDIVISIONS = {"v", "x", "y", "z"}


def _qualifiers(claim):
    return {p: [r["value"] for r in claim["qualifiers"] if r["property"] == p]
            for p in {r["property"] for r in claim["qualifiers"]}}


def _source_reference(claim, cn, proof_urls=(), proof_text=None):
    return any(
        any(s.get("datavalue", {}).get("value") == cn for s in r.get("snaks", {}).get("P3959", []))
        and set(proof_urls) <= {_snak_value(snak) for snak in r.get("snaks", {}).get("P854", [])}
        and (not proof_text or any(
            snak.get("datavalue", {}).get("value") == {"text": proof_text, "language": "en"}
            for snak in r.get("snaks", {}).get("P1683", [])
        ))
        for r in claim["references"]
    )


def matching_claims(entity, pid, value, qualifiers, *, language=None):
    matches = [
        claim for claim in _live_claims(entity, pid)
        if claim["value"] == value and _qualifiers(claim) == {p: [v] for p, v in qualifiers.items()}
    ]
    if language:
        native = {raw.get("id"): raw for raw in entity.get("claims", {}).get(pid, [])}
        matches = [
            claim for claim in matches
            if isinstance(raw_value := native[claim["claim_id"]]["mainsnak"]
                          .get("datavalue", {}).get("value"), Mapping)
            and raw_value.get("language") == language
        ]
    return matches


def _same_inscription_scope(left, right):
    left_role = left.get("P3831", [])
    right_role = right.get("P3831", [])
    if left_role and right_role and not set(left_role).intersection(right_role):
        return False
    left_folio = left.get("P7416", [])
    right_folio = right.get("P7416", [])
    return not left_folio or not right_folio or bool(set(left_folio).intersection(right_folio))


def source_case(cn, occurrence, source, reason, *, entity=None, pid=None, value=None,
                qualifiers=None, kind=None, language=None, dependencies=(), proof_urls=(), proof_text=None):
    """Retain every source occurrence and the exact evidence for eligible cases."""
    case = {
        "point": 8, "field": occurrence, "occurrence": occurrence, "source_field": occurrence,
        "source_value": source, "verified": False, "reason": reason,
        "classification": "identity_or_scope_uncertain",
    }
    if not entity or not pid or not value:
        return case
    qualifiers = qualifiers or {}
    existing = matching_claims(entity, pid, value, qualifiers, language=language)
    case.update(property=pid, expected_value=value)
    same_value = [
        claim for claim in entity.get("claims", {}).get(pid, [])
        if _snak_value(claim["mainsnak"]) == value
    ]
    if len(same_value) > len(existing) or len(existing) > 1 or (
        existing and not existing[0]["claim_id"]
    ):
        case["reason"] = "ambiguous_source_claim"
        return case
    if existing and _source_reference(existing[0], cn, proof_urls, proof_text):
        case.update(
            verified=True, reason="verified_source_assertion",
            verified_target_qid=entity["id"], verified_target_revision=entity["lastrevid"],
            expected_qualifiers=qualifiers,
            verified_matches=[{"statement_id": existing[0]["claim_id"], "value": value}],
        )
        return case
    if pid == "P1684" and not existing and any(
        _same_inscription_scope({p: [v] for p, v in qualifiers.items()}, _qualifiers(claim))
        for claim in _live_claims(entity, pid)
    ):
        case["reason"] = "inscription_text_overlap"
        return case
    case.update(
        reason="source_reference_missing" if existing else "source_statement_missing",
        classification="present_not_transferred",
        source_repair={
            "qid": entity["id"], "revision": entity["lastrevid"], "property": pid, "value": value,
            "qualifiers": qualifiers, "type": kind, "language": language,
            "dependencies": {e["id"]: e["lastrevid"] for e in dependencies},
            **({"proof_urls": list(proof_urls)} if proof_urls else {}),
            **({"proof_text": proof_text} if proof_text else {}),
        },
    )
    return case


def _reviewed_inscriptions(cn, occurrence, source, entity, proof):
    inscriptions = [proof, *proof["additional_inscriptions"]]
    folios = {row["qualifiers"].get("P7416") for row in inscriptions}
    if None in folios or "" in folios or len(folios) != len(inscriptions):
        return source_case(cn, occurrence, source, "inscription_text_overlap")
    permitted_claims = {
        match["claim_id"]
        for row in inscriptions
        for match in matching_claims(
            entity, "P1684", row["value"], row["qualifiers"], language=row["language"],
        )
    }
    if any(claim.get("id") not in permitted_claims for claim in entity.get("claims", {}).get("P1684", [])):
        return source_case(cn, occurrence, source, "inscription_text_overlap")
    cases = [
        source_case(
            cn, occurrence, source, "reviewed_source_proof", entity=entity, pid="P1684",
            value=row["value"], qualifiers=row["qualifiers"], kind="monolingualtext",
            language=row["language"], proof_urls=(row["proof_url"],),
        )
        for row in inscriptions
    ]
    pending = [row for row in cases if not row["verified"]]
    if not pending:
        return {
            **cases[0],
            "verified_matches": [
                {**match, "qualifiers": row["expected_qualifiers"]}
                for row in cases for match in row["verified_matches"]
            ],
        }
    if any(not row.get("source_repair") for row in pending):
        return source_case(cn, occurrence, source, "inscription_text_overlap")
    first, *others = pending
    return {
        **first,
        "source_repair": {
            **first["source_repair"],
            "additional_claims": [row["source_repair"] for row in others],
        },
    }


def _reviewed_work_source(cn, occurrence, source, manuscript, proof, entities):
    pid, value = proof["property"], proof["value"]
    target = _work_target(manuscript, entities, _work_title_key(proof["target_work_title"]), cn)
    topic = entities.get(value)
    if (
        not proof.get("supporting_note") or not topic
        or pid not in {"P921", "P136"} or proof["type"] != "item"
        or target.get("id") != proof["target_work_qid"]
    ):
        return source_case(cn, occurrence, source, "reviewed_work_scope_changed")
    heading = " — ".join(
        part["value"] for part in proof.get("heading_subfields", [])
        if part["code"] in _TOPIC_SUBDIVISIONS or part["code"] == "a"
    )
    return source_case(
        cn, occurrence, source, "reviewed_source_proof", entity=target, pid=pid,
        value=value, qualifiers=proof["qualifiers"], kind="item",
        proof_urls=tuple(dict.fromkeys((proof["proof_url"], *proof.get("authority_urls", ())))),
        proof_text=heading, dependencies=(manuscript, topic),
    )


def _reviewed_work_heading(row, proof):
    tag = row["tag"]
    if {"650": "P921", "655": "P136"}.get(tag) != proof["property"] or marc.values(row, "2") != ["nli"]:
        return False
    heading = proof.get("heading_subfields")
    if heading is not None:
        return tag == "650" and heading == row["subfields"]
    return not any(marc.values(row, code) for code in _TOPIC_SUBDIVISIONS)


def _reviewed_source_case(cn, occurrence, source, entity, proof, entities):
    pid, value = proof["property"], proof["value"]
    if proof.get("target_work_qid"):
        return _reviewed_work_source(cn, occurrence, source, entity, proof, entities)
    if pid == "P1684" and proof.get("additional_inscriptions"):
        return _reviewed_inscriptions(cn, occurrence, source, entity, proof)
    qualifiers, language = proof["qualifiers"], proof.get("language")
    exact = matching_claims(entity, pid, value, qualifiers, language=language)
    if proof.get("verification_only") and (
        len(exact) != 1 or not _source_reference(exact[0], cn, (proof["proof_url"],))
    ):
        return source_case(cn, occurrence, source, "reviewed_existing_source_changed")
    if pid == "P1684" and not exact and entity.get("claims", {}).get(pid):
        return source_case(cn, occurrence, source, "inscription_text_overlap")
    if pid == "P953":
        if claim_id := proof.get("claim_id"):
            confirmed = len(exact) == 1 and exact[0]["claim_id"] == claim_id
        else:
            other_urls = Counter(
                _snak_value(claim["mainsnak"]) for claim in entity.get("claims", {}).get(pid, [])
                if not exact or claim.get("id") != exact[0]["claim_id"]
            )
            prior_urls = proof.get("prior_access_urls")
            confirmed = prior_urls is not None and other_urls == Counter(prior_urls)
        if not confirmed:
            return source_case(cn, occurrence, source, "access_link_requires_holding_proof")
    dependency = entities.get(value) if proof["type"] == "item" else None
    if proof["type"] == "item" and not dependency:
        return source_case(cn, occurrence, source, "genre_scope_or_identity_required")
    return source_case(
        cn, occurrence, source, "reviewed_source_proof", entity=entity, pid=pid, value=value,
        qualifiers=qualifiers, kind=proof["type"], language=language, proof_urls=(proof["proof_url"],),
        dependencies=(dependency,) if dependency else (),
    )


def _quoted_inscription(row, source):
    if row["tag"] == "957" and (label := _COLOPHON_LABEL_RE.match(source)):
        role = "Q372474"
        folio = re.sub(r"^\s*(?:בדף|דף)\s+", "", label.group("folio") or "").strip()
    elif row["tag"] == "500" and (label := _TITLE_PAGE_PREFIX_RE.match(source)):
        role = "Q1339862"
        folio = ""
    else:
        return None
    text = source[label.end():].strip().replace('""', '"')
    if text.startswith('"') and text.endswith('".'):
        text = text[1:-2].strip()
    elif text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    else:
        # A label may introduce a librarian's description.
        return None
    if not text or len(text) > 1500 or is_catalog_note_placeholder(text):
        return None
    qualifiers = {"P3831": role}
    if folio:
        qualifiers["P7416"] = folio
    if not re.search(r"[א-ת]", text):
        return None
    return text, qualifiers


def _single_source_work(record, entity, entities, cn):
    if not entity or len({c["value"] for c in _live_claims(entity, "P1574")}) != 1:
        return {}
    fields = marc.occurrences(record)
    if sum(row["tag"] == "245" for row in fields) != 1 or any(
        row["tag"] in {"505", "730", "740", "773", "774"}
        or (row["tag"] in {"700", "710", "711", "800", "810", "811"} and marc.values(row, "t"))
        for row in fields
    ):
        return {}
    return _work_target(entity, entities, _work_title_key(record.get("title")), cn)


def _scan_property(row, url, entity):
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if (
        parsed.scheme != "https" or parsed.hostname not in _SCAN_HOSTS
        or parsed.netloc != parsed.hostname or not entity or row["indicators"][1] not in {" ", "0", "1"}
        or any(marc.values(row, c) for c in _COPY_LIMITS)
        or not any(_ONLINE.fullmatch(text.strip()) for text in marc.values(row, "z"))
    ):
        return None
    # The current statement and the exact MARC URL identify the digital copy.
    if any(c["value"] == url for c in _live_claims(entity, "P953")):
        return "P953"
    return None


def source_colophon(record):
    if not record:
        return ""
    texts = [
        result[0] for row in marc.occurrences(record, "957") for source in marc.values(row, "a")
        if (result := _quoted_inscription(row, source.strip()))
    ]
    return texts[0] if len(set(texts)) == 1 else ""


def source_topic_ids(records):
    """Return the static crosswalk targets for the public snapshot."""
    return {
        qid for record in records for row in marc.occurrences(record)
        if row["tag"] in {"650", "655"}
        for term in marc.values(row, "a")
        if (qid := (_static_genre_qid if row["tag"] == "655" else _static_subject_qid)(term))
    } | reviewed_source_items()


def full_source_cases(cn, raw, project_record, entity, entities, catalog_choices):
    if not raw:
        yield source_case(cn, "001:full-source", cn, "full_marc_unavailable")
        return
    record = prepare_record_for_pipeline(dict(raw))
    source_entity = entity if entity and set(_claim_values(entity, "P3959")) == {cn} else None
    work = _single_source_work(record, source_entity, entities, cn)
    source_rows = marc.occurrences(raw)
    quotations = [
        quotation for row in source_rows if row["tag"] in {"500", "957"}
        for source in marc.values(row, "a")
        if (quotation := _quoted_inscription(row, source.strip()))
    ]
    full_citations = Counter()
    codes = Counter(
        " ".join(catalog_reference_text(ref).split())
        for ref in (project_record.get("catalog_references") or []) if isinstance(ref, Mapping)
        and ref.get("source_field") in {"510", "581"} and catalog_reference_code(ref)
    )
    legacy_citations = Counter(
        " ".join(catalog_reference_text(ref).split())
        for ref in (project_record.get("catalog_references") or []) if isinstance(ref, Mapping)
        and ref.get("source_field") in {"510", "581"} and not catalog_reference_code(ref)
        and catalog_reference_text(ref).strip()
    )
    for row in source_rows:
        tag = row["tag"]
        if tag not in {"500", "510", "581", "590", "650", "655", "856", "957"}:
            continue
        group = marc.source_key(row)
        scopes = any(marc.values(row, code) for code in _COPY_LIMITS)
        for ordinal, source in enumerate(marc.values(row, "u" if tag == "856" else "a"), 1):
            occurrence = f"{group}:{ordinal}"
            source = source.strip()
            if not source:
                continue
            reviewed = reviewed_source_proof(cn, occurrence, source, source_entity, source_rows)
            if reviewed and reviewed.get("target_work_qid") and not _reviewed_work_heading(row, reviewed):
                reviewed = None
            if tag in {"510", "581"}:
                normalized = " ".join(source.split())
                if codes[normalized]:
                    codes[normalized] -= 1
                    continue
                full_citations[normalized] += 1
                choice = catalog_choices.get((tag, source))
                if choice and choice["target_identity_current"] and not scopes:
                    proof = choice["proof"]
                    publication = entities.get(proof["target_qid"])
                    pages = str(proof["candidate"].get("pages") or "")
                    yield source_case(
                        cn, occurrence, source, "bibliographic_source_identity_unresolved",
                        entity=source_entity if publication else None,
                        pid="P1343", value=proof["target_qid"], qualifiers={"P304": pages} if pages else {},
                        kind="item", dependencies=(publication,) if publication else (),
                    )
                elif reviewed and not scopes:
                    yield _reviewed_source_case(cn, occurrence, source, source_entity, reviewed, entities)
                else:
                    yield source_case(cn, occurrence, source, "bibliographic_source_identity_unresolved")
            elif reviewed and not scopes:
                yield _reviewed_source_case(cn, occurrence, source, source_entity, reviewed, entities)
            elif tag == "590":
                yield source_case(cn, occurrence, source, "catalogue_status_evidence")
            elif tag in {"500", "957"}:
                inscription = _quoted_inscription(row, source)
                if not inscription:
                    if tag == "957" or _TITLE_PAGE_PREFIX_RE.match(source):
                        yield source_case(cn, occurrence, source, "inscription_requires_transcription_proof")
                    continue
                text, qualifiers = inscription
                overlapping = any(
                    other_text != text and _same_inscription_scope(
                        {p: [v] for p, v in qualifiers.items()}, {p: [v] for p, v in other_scope.items()},
                    )
                    for other_text, other_scope in quotations
                )
                yield source_case(cn, occurrence, source,
                                  "inscription_text_overlap" if overlapping else "inscription_requires_transcription_proof",
                                  entity=None if overlapping else source_entity, pid="P1684", value=text, qualifiers=qualifiers,
                                  kind="monolingualtext", language="he")
            elif tag == "856":
                pid = _scan_property(row, source, source_entity)
                yield source_case(cn, occurrence, source, "access_link_requires_holding_proof",
                                  entity=source_entity, pid=pid, value=source, kind="url")
            elif tag in {"650", "655"}:
                qualified = scopes or any(marc.values(row, c) for c in _TOPIC_SUBDIVISIONS)
                value = None
                if work and not qualified:
                    if tag == "650" and row["indicators"][0] in {" ", "0", "1", "2"}:
                        value = _static_subject_qid(source)
                    if tag == "655" and genre_projection_supported(source, record):
                        value = _static_genre_qid(source)
                genre = tag == "655"
                yield source_case(
                    cn, occurrence, source, "genre_scope_or_identity_required" if genre else "subject_scope_or_identity_required",
                    entity=work if value in entities else None, pid="P136" if genre else "P921", value=value, kind="item",
                    dependencies=tuple(e for e in (source_entity, entities.get(value)) if e),
                )
    for ordinal, text in enumerate(sorted((legacy_citations - full_citations).elements()), 1):
        yield source_case(cn, f"581:project:{ordinal}", text, "citation_differs_from_full_marc")
