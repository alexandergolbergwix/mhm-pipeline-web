"""Merge approved authority + NER rows into flat MARC dicts before RDF mapping."""

from __future__ import annotations

from typing import Any

from converter.rdf.rdf_helpers import (
    clean_marc_label,
    ensure_person_in_list,
    names_overlap,
    normalize_role,
)


def merge_approved_ner(rec: dict[str, Any], entities: list[dict[str, Any]]) -> None:
    """Fold approved Stage-2 entities into *rec* field lists."""
    for ent in entities:
        src = ent.get("source", "")
        text = clean_marc_label(str(ent.get("text") or ""))
        if not text:
            continue
        ent_type = (ent.get("type") or "").upper()
        role = (ent.get("role") or "").upper()

        if src == "person_ner":
            target_key = "authors" if role == "AUTHOR" else "contributors"
            people: list[dict[str, Any]] = rec.setdefault(target_key, []) or []
            existing = {clean_marc_label(str(a.get("name") or "")).casefold() for a in people}
            if text.casefold() not in existing:
                entry: dict[str, Any] = {
                    "name": text,
                    "role": normalize_role(role.lower() or "related"),
                    "source": "person_ner",
                }
                if ent.get("confidence") is not None:
                    entry["ner_confidence"] = ent["confidence"]
                people.append(entry)
            rec[target_key] = people

        elif src == "genre_ml":
            genres: list[str] = rec.setdefault("genres", []) or []
            if text not in genres:
                genres.append(text)
            rec["genres"] = genres
            key = f"genre_{text}"
            rec.setdefault("attribution_sources", {})[key] = "AIAttribution"
            rec.setdefault("certainty_levels", {})[key] = "Probable"
            rec.setdefault("certainty_levels", {})[f"{key}_note"] = (
                "Inferred by genre classifier"
            )

        elif src == "contents_ner":
            if ent_type == "WORK":
                contents: list[dict[str, Any]] = rec.setdefault("contents", []) or []
                titles = {clean_marc_label(str(c.get("title") or "")).casefold() for c in contents}
                if text.casefold() not in titles:
                    contents.append({"title": text, "source": "contents_ner"})
                rec["contents"] = contents
            elif ent_type == "FOLIO":
                folio = text
                contents_list: list[dict[str, Any]] = rec.setdefault("contents", []) or []
                if contents_list:
                    contents_list[-1].setdefault("folio_range", folio)
                else:
                    rec.setdefault("contents", []).append({"title": "", "folio_range": folio})
            elif ent_type == "WORK_AUTHOR" and text:
                contents_list = rec.setdefault("contents", []) or []
                if contents_list:
                    contents_list[-1]["author"] = text

        elif src == "provenance_ner":
            if ent_type == "OWNER":
                people = rec.setdefault("contributors", []) or []
                ensure_person_in_list(people, text, role="former_owner", extra={"source": "provenance_ner"})
                rec["contributors"] = people
            elif ent_type == "DATE":
                events = rec.setdefault("provenance_events", []) or []
                events.append({
                    "type": "ownership",
                    "place_text": "",
                    "agent_name": None,
                    "year": _parse_year(text),
                    "source_field": "provenance_ner",
                    "lat": None,
                    "lon": None,
                    "wikidata_id": None,
                    "certain": _parse_year(text) is not None,
                })
                rec["provenance_events"] = events
            elif ent_type == "COLLECTION":
                refs = rec.setdefault("catalog_references", []) or []
                if text not in refs:
                    refs.append(text)
                rec["catalog_references"] = refs

        if ent.get("authority_id") or ent.get("wikidata_qid"):
            _merge_entity_authority(rec, text, ent)


def merge_approved_authority(rec: dict[str, Any], matches: list[dict[str, Any]]) -> None:
    """Fold approved authority matches into *rec* (persons + places)."""
    marc_matches: list[dict[str, Any]] = rec.setdefault("marc_authority_matches", []) or []

    for m in matches:
        payload = m.get("payload") or {}
        entity_text = clean_marc_label(str(m.get("entity_text") or ""))
        if not entity_text:
            continue

        match_row = {
            "entity_text": entity_text,
            "entity_kind": m.get("entity_kind") or "person",
            "role": m.get("role") or "",
            "wikidata_qid": m.get("wikidata_qid"),
            "viaf_id": m.get("viaf_id"),
            "mazal_id": m.get("mazal_id"),
            "payload": payload,
        }
        if match_row not in marc_matches:
            marc_matches.append(match_row)

        is_kima = bool(payload.get("kima_id"))
        entity_kind = (m.get("entity_kind") or "person").lower()

        if is_kima or entity_kind == "place":
            _merge_kima_place(rec, entity_text, m, payload)
            continue

        if entity_kind == "topic":
            _merge_topic_authority(rec, entity_text, m, payload)
            continue

        if entity_kind == "work":
            _merge_work_authority(rec, entity_text, m, payload)
            continue

        if entity_kind in ("corporate", "organization", "meeting"):
            _merge_corporate_authority(rec, entity_text, m, payload)
            continue

        role = normalize_role(str(m.get("role") or "contributor"))
        target_key = "authors" if role == "author" else "contributors"
        people = rec.setdefault(target_key, []) or []
        matched = False
        for person in people:
            if names_overlap(str(person.get("name") or ""), entity_text):
                _fill_person_authority(person, m, payload)
                matched = True
                break
        if not matched:
            extra: dict[str, Any] = {"source": "authority_match"}
            if m.get("viaf_id"):
                extra["viaf_id"] = str(m["viaf_id"])
            if m.get("wikidata_qid"):
                extra["wikidata_id"] = m["wikidata_qid"]
            if m.get("mazal_id"):
                extra["authority_id"] = str(m["mazal_id"])
            _fill_person_authority(extra, m, payload)
            ensure_person_in_list(people, entity_text, role=role, extra=extra)
            rec[target_key] = people

    rec["marc_authority_matches"] = marc_matches


def merge_ml_genres(rec: dict[str, Any], ml_genres: list[dict[str, Any]]) -> None:
    """Fold Stage-2 ``ml_genres`` predictions into *rec* when MARC 655 is empty."""
    if rec.get("genres"):
        return
    genres: list[str] = rec.setdefault("genres", []) or []
    attr = rec.setdefault("attribution_sources", {})
    certainty = rec.setdefault("certainty_levels", {})
    for item in ml_genres:
        label = str(item.get("label") or "").strip()
        if not label or label == "other":
            continue
        if label not in genres:
            genres.append(label)
        key = f"genre_{label}"
        attr[key] = "AIAttribution"
        certainty[key] = "Probable"
        certainty[f"{key}_note"] = "Inferred by genre classifier"
    if genres:
        rec["genres"] = genres


def apply_genre_classifier_fallback(rec: dict[str, Any]) -> None:
    """Run the genre classifier when ``genres`` is still empty (RDF / Wikidata parity)."""
    if rec.get("genres"):
        return
    from converter.wikidata.item_builder import _get_genre_classifier  # noqa: PLC0415

    clf = _get_genre_classifier()
    if clf is None:
        return
    title = str(rec.get("title") or "").strip()
    notes_list = [str(n) for n in (rec.get("notes") or []) if n]
    try:
        inferred = clf.predict(title, notes_list)
    except Exception:  # noqa: BLE001
        return
    ml_items = [{"label": label, "confidence": conf} for label, conf in inferred]
    merge_ml_genres(rec, ml_items)


def merge_kima_places_dict(rec: dict[str, Any], kima_places: dict[str, str]) -> None:
    """Attach run-level ``kima_places`` name→URI map for graph_builder."""
    if not kima_places:
        return
    merged = dict(rec.get("kima_places") or {})
    merged.update(kima_places)
    rec["kima_places"] = merged


def _fill_person_authority(
    person: dict[str, Any],
    match: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    if match.get("viaf_id") and "viaf_id" not in person:
        person["viaf_id"] = str(match["viaf_id"])
    if match.get("wikidata_qid") and "wikidata_id" not in person:
        person["wikidata_id"] = match["wikidata_qid"]
    if match.get("mazal_id") and "authority_id" not in person:
        person["authority_id"] = str(match["mazal_id"])
    if match.get("mazal_id") and "mazal_id" not in person:
        person["mazal_id"] = str(match["mazal_id"])
    if payload.get("mazal_aleph_id") and "mazal_id" not in person:
        person["mazal_id"] = str(payload["mazal_aleph_id"])
        if "authority_id" not in person:
            person["authority_id"] = str(payload["mazal_aleph_id"])
    if payload.get("birth_year") is not None and "birth_year" not in person:
        person["birth_year"] = payload["birth_year"]
    if payload.get("death_year") is not None and "death_year" not in person:
        person["death_year"] = payload["death_year"]
    if payload.get("preferred_name_lat") and "preferred_name_lat" not in person:
        person["preferred_name_lat"] = payload["preferred_name_lat"]
    if payload.get("preferred_name_heb") and "preferred_name_heb" not in person:
        person["preferred_name_heb"] = payload["preferred_name_heb"]
    if payload.get("viaf_uri") and "viaf_uri" not in person:
        person["viaf_uri"] = payload["viaf_uri"]
    cluster = payload.get("cluster_ids") or {}
    for id_key in ("gnd", "lc", "isni", "bnf", "j9u"):
        if cluster.get(id_key) and id_key not in person:
            person[id_key] = cluster[id_key]
    biodata = payload.get("biodata_authority")
    if isinstance(biodata, dict) and biodata and "authority_biodata" not in person:
        person["authority_biodata"] = biodata
    occupations = payload.get("occupations")
    if isinstance(occupations, list) and occupations and "occupations" not in person:
        person["occupations"] = occupations


def _merge_topic_authority(
    rec: dict[str, Any],
    entity_text: str,
    match: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """Stamp approved authority IDs onto MARC 650 topical subject rows."""
    from converter.transformer.subject_records import subject_term  # noqa: PLC0415

    for subj in rec.get("subjects") or []:
        if not isinstance(subj, dict):
            continue
        if str(subj.get("type") or "") != "topic":
            continue
        term = subject_term(subj)
        if not term or not names_overlap(term, entity_text):
            continue
        if match.get("wikidata_qid") and "wikidata_id" not in subj:
            subj["wikidata_id"] = match["wikidata_qid"]
        if match.get("mazal_id") and "mazal_id" not in subj:
            subj["mazal_id"] = str(match["mazal_id"])
        if match.get("viaf_id") and "viaf_id" not in subj:
            subj["viaf_id"] = str(match["viaf_id"])
        auth = payload.get("mazal_aleph_id") or match.get("mazal_id")
        if auth and "authority_id" not in subj:
            subj["authority_id"] = str(auth)


def _stamp_authority_ids(
    target: dict[str, Any],
    match: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    if match.get("wikidata_qid") and "wikidata_id" not in target:
        target["wikidata_id"] = match["wikidata_qid"]
    if match.get("mazal_id") and "mazal_id" not in target:
        target["mazal_id"] = str(match["mazal_id"])
    if match.get("viaf_id") and "viaf_id" not in target:
        target["viaf_id"] = str(match["viaf_id"])
    auth = payload.get("mazal_aleph_id") or match.get("mazal_id")
    if auth and "authority_id" not in target:
        target["authority_id"] = str(auth)


def _merge_work_authority(
    rec: dict[str, Any],
    entity_text: str,
    match: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """Stamp approved authority IDs onto work titles (505 / work_mentions)."""
    for content in rec.get("contents") or []:
        if not isinstance(content, dict):
            continue
        title = clean_marc_label(str(content.get("title") or ""))
        if title and names_overlap(title, entity_text):
            _stamp_authority_ids(content, match, payload)

    for wm in rec.get("work_mentions") or []:
        if not isinstance(wm, dict):
            continue
        title = clean_marc_label(str(wm.get("title") or ""))
        if title and names_overlap(title, entity_text):
            _stamp_authority_ids(wm, match, payload)


def _merge_corporate_authority(
    rec: dict[str, Any],
    entity_text: str,
    match: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """Stamp approved authority IDs onto corporate subjects and contributors."""
    from converter.transformer.subject_records import subject_term  # noqa: PLC0415

    for subj in rec.get("subjects") or []:
        if not isinstance(subj, dict):
            continue
        if str(subj.get("type") or "") not in ("organization", "corporate", "meeting"):
            continue
        term = subject_term(subj)
        if term and names_overlap(term, entity_text):
            _stamp_authority_ids(subj, match, payload)

    for person in (rec.get("contributors") or []) + (rec.get("authors") or []):
        if not isinstance(person, dict):
            continue
        name = clean_marc_label(str(person.get("name") or ""))
        if name and names_overlap(name, entity_text):
            _stamp_authority_ids(person, match, payload)

    refs = rec.get("catalog_references") or []
    for i, ref in enumerate(refs):
        ref_text = clean_marc_label(str(ref))
        if ref_text and names_overlap(ref_text, entity_text):
            if isinstance(ref, str):
                entry = {"name": ref_text}
                _stamp_authority_ids(entry, match, payload)
                refs[i] = entry
            elif isinstance(ref, dict):
                _stamp_authority_ids(ref, match, payload)
    if refs:
        rec["catalog_references"] = refs


def _merge_kima_place(
    rec: dict[str, Any],
    entity_text: str,
    match: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    from converter.transformer.subject_records import subject_term  # noqa: PLC0415

    kima_lat = payload.get("kima_lat")
    kima_lon = payload.get("kima_lon")
    kima_geo = payload.get("kima_geonames")
    wikidata_qid = match.get("wikidata_qid") or payload.get("wikidata_id") or payload.get("wikidata_qid")
    kima_id = payload.get("kima_id")
    viaf_id = match.get("viaf_id") or payload.get("viaf_id") or payload.get("kima_viaf_id")
    mazal_id = match.get("mazal_id") or payload.get("mazal_id") or payload.get("mazal_nli_id")

    for subj in rec.get("subjects") or []:
        term = subject_term(subj) if isinstance(subj, dict) else ""
        if term and names_overlap(term, entity_text):
            if kima_geo and "geonames_id" not in subj:
                subj["geonames_id"] = str(kima_geo)
            if kima_lat is not None and "lat" not in subj:
                subj["lat"] = kima_lat
            if kima_lon is not None and "lon" not in subj:
                subj["lon"] = kima_lon
            if wikidata_qid and "wikidata_id" not in subj:
                subj["wikidata_id"] = wikidata_qid
            if kima_id and "kima_id" not in subj:
                subj["kima_id"] = str(kima_id)
            if viaf_id and "viaf_id" not in subj:
                subj["viaf_id"] = str(viaf_id)
            if mazal_id and "mazal_id" not in subj:
                subj["mazal_id"] = str(mazal_id)

    prod_place = clean_marc_label(str(rec.get("place") or ""))
    if prod_place and names_overlap(prod_place, entity_text):
        if wikidata_qid:
            rec.setdefault("production_place_wikidata_id", str(wikidata_qid))
        if kima_id:
            rec.setdefault("production_place_kima_id", str(kima_id))
        if viaf_id:
            rec.setdefault("production_place_viaf_id", str(viaf_id))
        if mazal_id:
            rec.setdefault("production_place_mazal_id", str(mazal_id))
        if kima_geo:
            rec.setdefault("production_place_geonames_id", str(kima_geo))

    if kima_lat is not None and kima_lon is not None:
        prod_place = clean_marc_label(str(rec.get("place") or ""))
        if prod_place and names_overlap(prod_place, entity_text):
            rec.setdefault("production_place_lat", kima_lat)
            rec.setdefault("production_place_lon", kima_lon)
            if wikidata_qid:
                rec.setdefault("production_place_wikidata_id", wikidata_qid)

        for rp_name in rec.get("related_places") or []:
            rp_clean = clean_marc_label(str(rp_name))
            if rp_clean and names_overlap(rp_clean, entity_text):
                coord_bucket = rec.setdefault("related_place_coords", {})
                entry = coord_bucket.setdefault(rp_clean, {})
                entry.setdefault("lat", kima_lat)
                entry.setdefault("lon", kima_lon)
                if wikidata_qid:
                    entry.setdefault("wikidata_id", wikidata_qid)
                if kima_id:
                    entry.setdefault("kima_id", str(kima_id))
                if viaf_id:
                    entry.setdefault("viaf_id", str(viaf_id))
                if mazal_id:
                    entry.setdefault("mazal_id", str(mazal_id))
                if kima_geo:
                    entry.setdefault("geonames_id", str(kima_geo))
                break

        for ev in rec.get("provenance_events") or []:
            if not isinstance(ev, dict):
                continue
            pt = clean_marc_label(str(ev.get("place_text") or ""))
            if pt and names_overlap(pt, entity_text):
                # build_provenance_event (marc_ingest.py) pre-populates
                # lat/lon/wikidata_id with explicit None placeholders, so
                # setdefault (which only fires when the key is absent, not
                # when its value is None) never actually filled them in —
                # provenance-event coords from KIMA were silently dropped.
                if ev.get("lat") is None:
                    ev["lat"] = kima_lat
                if ev.get("lon") is None:
                    ev["lon"] = kima_lon
                if wikidata_qid and ev.get("wikidata_id") is None:
                    ev["wikidata_id"] = wikidata_qid
                if kima_geo:
                    ev.setdefault("geonames_id", str(kima_geo))


def _merge_entity_authority(rec: dict[str, Any], entity_text: str, ent: dict[str, Any]) -> None:
    for target_key in ("authors", "contributors"):
        for person in rec.get(target_key) or []:
            if names_overlap(str(person.get("name") or ""), entity_text):
                if ent.get("wikidata_qid") and "wikidata_id" not in person:
                    person["wikidata_id"] = ent["wikidata_qid"]
                if ent.get("authority_id") and "authority_id" not in person:
                    person["authority_id"] = str(ent["authority_id"])


def _parse_year(text: str) -> int | None:
    import re

    m = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", text)
    if m:
        return int(m.group(1))
    return None
