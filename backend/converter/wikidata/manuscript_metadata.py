"""Labels and manuscript metadata projection helpers."""

from __future__ import annotations

import re

from converter.wikidata.item_builder import (
    LANG_TO_QID,
    MATERIAL_TO_QID,
    P_HEIGHT,
    P_LANGUAGE,
    P_MATERIAL,
    P_NUMBER_OF_PAGES,
    P_OBJECT_NAMED_AS,
    P_OWNED_BY,
    P_START_TIME,
    P_WIDTH,
    P_WRITING_SYSTEM,
    Q_COMPOSITE_MANUSCRIPT,
    Q_HEBREW_ALPHABET,
    Q_ILLUMINATED_MANUSCRIPT,
    Q_LEAF_UNIT,
    Q_MANUSCRIPT,
    Q_PRINTED_BOOK,
    Q_PALIMPSEST,
    WikidataItem,
    WikidataStatement,
    _QUOTE_CHARS,
    _build_manuscript_description,
    _has_hebrew_script,
    _is_placeholder_title,
    _is_printed_facsimile_record,
    _normalise_label,
    _person_key,
)
from converter.wikidata.property_mapping import (
    DISCOURAGED_MANUSCRIPT_P31,
    FRAGMENT_CONDITION_KEYWORDS,
    Q_MANUSCRIPT_FRAGMENT,
    materials_in_text,
)


class ManuscriptMetadataMixin:
    def _set_labels(
        self,
        item: WikidataItem,
        record: dict[str, object],
        title: str,
    ) -> None:
        """Set labels, descriptions, and aliases for a manuscript item.

        Bug fix 2026-04-15 (Geagea complaint): MARC 245 sometimes contains
        a generic placeholder like "קובץ." (= "compilation") rather than a
        real title, used by catalogers when an anthology has no overarching
        name. Emitting that as the Hebrew label produced 94 useless labels
        on Wikidata. We now detect placeholder titles and route them to
        an alias slot, falling back to a shelfmark-based label.
        """
        is_placeholder = _is_placeholder_title(title)
        shelfmark = _normalise_label(str(record.get("shelfmark") or ""))
        from converter.wikidata.item_builder import _holding_institution_name
        holding = _holding_institution_name(record)
        title_clean = _normalise_label(title) if title else ""
        title_has_hebrew = _has_hebrew_script(title_clean)
        if title_clean and not is_placeholder:
            # Latin-only titles (e.g. "Meir Netiv in Latin", "Referat über
            # den XI Zionistischen Kongress in Basel") must NOT go into the
            # he-label slot — the validator flags that as HE_LABEL_IS_LATIN.
            # Route them to the en slot only and keep them as an en-alias for
            # searchability (the en label may later be overwritten by the
            # shelfmark-based form).
            if title_has_hebrew:
                item.labels["he"] = title_clean
                # Hebrew text must NOT go into the en label slot. The en label
                # is set below to the shelfmark-based form ("Jerusalem, NLI,
                # <shelfmark>"). Without a shelfmark the en slot stays absent,
                # which is correct — an empty slot is far preferable to a
                # Hebrew string masquerading as an English label.
            else:
                item.labels["en"] = title_clean
                item.aliases.setdefault("en", []).append(title_clean)
        elif title:
            # Placeholder: keep the original cataloger string as a Hebrew
            # alias for searchability, but do NOT use it as the label.
            item.aliases.setdefault("he", []).append(_normalise_label(title))

        if shelfmark:
            # The catalog is NLI, but the physical holder may be a library or
            # archive named in MARC 710. Never claim Jerusalem/NLI ownership
            # when the record explicitly identifies another current holder.
            label_prefix = holding or "Jerusalem, NLI"
            item.labels["en"] = f"{label_prefix}, {shelfmark}"
            if title_clean and not is_placeholder and title_has_hebrew:
                item.aliases.setdefault("he", []).append(title_clean)
            # When the title was a placeholder OR Latin-only AND we have a
            # shelfmark, synthesise a useful Hebrew label from the shelfmark.
            if "he" not in item.labels and (is_placeholder or not title_has_hebrew):
                item.labels["he"] = f"כתב יד עברי, ספרייה לאומית, {shelfmark}"

        if "en" not in item.labels and "he" not in item.labels:
            cn = str(record.get("_control_number") or record.get("control_number") or "").strip()
            if cn:
                item.labels["en"] = f"Jerusalem, NLI, {cn}"
                item.labels["he"] = f"כתב יד עברי, ספרייה לאומית, {cn}"

        # Variant titles as aliases
        for vt in record.get("variant_titles") or []:
            vt_clean = _normalise_label(str(vt))
            if vt_clean:
                item.aliases.setdefault("he", []).append(vt_clean)

        # Description — language, date/century, script tradition, material, NLI
        item.descriptions["en"] = _build_manuscript_description(record)

    def _determine_instance_type(self, record: dict[str, object]) -> list[str]:
        """Return all applicable P31 QIDs, most-specific first.

        Always emit at least Q87167 (manuscript) as the base type, placed last.
        WikiProject Manuscripts endorses multi-P31 when classes are not in a
        subclass tangle, but discourages Q213924 (codex) as a primary class —
        multi-volume / anthology strata use Q_COMPOSITE_MANUSCRIPT instead.
        """
        qids: list[str] = []
        if _is_printed_facsimile_record(record):
            return [Q_PRINTED_BOOK]
        from converter.wikidata.marc_subject_resolve import (  # noqa: PLC0415
            genre_projection_supported,
            illuminated_instance_supported,
        )

        illustrated_entries = [
            genre
            for genre in (
                list(record.get("genres") or [])
                + list(record.get("genre_entries") or [])
            )
            if isinstance(genre, dict)
            and str(genre.get("term") or genre.get("name") or "").strip().casefold()
            == "illustrated works (manuscript)"
        ]
        has_illustrated_genre = any(
            (str(genre.get("term") or genre.get("name") or "")
             if isinstance(genre, dict) else str(genre)).strip().casefold()
            == "illustrated works (manuscript)"
            for genre in (
                list(record.get("genres") or [])
                + list(record.get("genre_entries") or [])
            )
        )
        if has_illustrated_genre and (
            illuminated_instance_supported(record)
            or any(
                illuminated_instance_supported(record, entry)
                for entry in illustrated_entries
            )
        ):
            qids.append(Q_ILLUMINATED_MANUSCRIPT)
        if (
            record.get("is_multi_volume")
            or record.get("is_anthology")
            or record.get("is_composite")
        ):
            qids.append(Q_COMPOSITE_MANUSCRIPT)
        if record.get("is_palimpsest"):
            qids.append(Q_PALIMPSEST)
        cond_blob = " ".join(str(c) for c in (record.get("condition_notes") or [])).casefold()
        if any(kw in cond_blob for kw in FRAGMENT_CONDITION_KEYWORDS):
            qids.append(Q_MANUSCRIPT_FRAGMENT)
        from converter.wikidata.marc_subject_resolve import instance_qids_from_genre_labels  # noqa: PLC0415

        supported_genres = [
            genre for genre in list(record.get("genres") or [])
            if genre_projection_supported(
                str(genre.get("term") or genre.get("name") or "")
                if isinstance(genre, dict)
                else str(genre),
                record,
                genre if isinstance(genre, dict) else None,
            )
        ]
        for qid in instance_qids_from_genre_labels(supported_genres):
            if qid in DISCOURAGED_MANUSCRIPT_P31:
                continue
            if qid not in qids:
                qids.append(qid)
        qids.append(Q_MANUSCRIPT)  # base type always last
        seen: set[str] = set()
        ordered: list[str] = []
        for qid in qids:
            if qid in DISCOURAGED_MANUSCRIPT_P31:
                continue
            if qid not in seen:
                seen.add(qid)
                ordered.append(qid)
        return ordered

    def _add_languages(
        self,
        item: WikidataItem,
        record: dict[str, object],
        ref: list[dict[str, str]],
    ) -> None:
        """Add P407 language and P282 writing system statements."""
        langs = record.get("languages") or []
        for lang_code in langs:
            qid = LANG_TO_QID.get(str(lang_code))
            if qid:
                item.statements.append(
                    WikidataStatement(
                        property_id=P_LANGUAGE,
                        value=qid,
                        value_type="item",
                        references=ref,
                    )
                )
        if any(str(c) in ("heb", "arc", "yid", "lad", "jrb", "jpr") for c in langs):
            item.statements.append(
                WikidataStatement(
                    property_id=P_WRITING_SYSTEM,
                    value=Q_HEBREW_ALPHABET,
                    value_type="item",
                    references=ref,
                )
            )

    def _add_physical_description(
        self,
        item: WikidataItem,
        record: dict[str, object],
        ref: list[dict[str, str]],
    ) -> None:
        """Add material, dimensions, folio count."""
        material_qids: list[str] = []
        for material in record.get("materials") or []:
            qid = MATERIAL_TO_QID.get(str(material))
            if qid and qid not in material_qids:
                material_qids.append(qid)
        if not material_qids:
            # 340$a is a free-text note in this corpus ("...על קלף", "קלף
            # בהיר.") — scan it for the SAME closed vocabulary rather than
            # inventing a material (Rule W-140).
            material_qids = materials_in_text(
                " ".join(
                    str(record.get(key) or "")
                    for key in ("material_note", "340$a", "physical_details")
                )
            )
        for qid in material_qids:
            item.statements.append(
                WikidataStatement(
                    property_id=P_MATERIAL,
                    value=qid,
                    value_type="item",
                    references=ref,
                )
            )
        height = record.get("height_mm")
        if height and float(height) > 0:
            item.statements.append(
                WikidataStatement(
                    property_id=P_HEIGHT,
                    value=float(height),
                    value_type="quantity",
                    unit="mm",
                    references=ref,
                )
            )
        width = record.get("width_mm")
        if width and float(width) > 0:
            item.statements.append(
                WikidataStatement(
                    property_id=P_WIDTH,
                    value=float(width),
                    value_type="quantity",
                    unit="mm",
                    references=ref,
                )
            )
        extent = record.get("extent")
        if extent:
            extent_str = str(extent)
            folio_match = re.search(r"(\d+)", extent_str)
            if folio_match:
                # The ingest parser already resolved the unit (Rule W-140);
                # only fall back to sniffing the string when it did not.
                parsed_unit = str(record.get("extent_unit") or "")
                # WikiProject Manuscripts Data Model (2026-06-04 audit):
                # ALL physical extent goes on P1104 (number of pages) with an
                # explicit unit.  P7416 "folio(s)" is a STRING QUALIFIER for
                # citing a source folio — it is NOT a count property.
                # Units: page (Q1069725) when the extent string says "page/עמוד",
                #        leaf (Q107256474) otherwise — manuscripts count in
                #        leaves/folios and WikiProject mandates the leaf unit.
                # https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts/Data_Model
                low = extent_str.lower()
                says_pages = (
                    parsed_unit == "page"
                    if parsed_unit
                    else ("page" in low or "עמוד" in low)
                )
                unit_qid = "Q1069725" if says_pages else Q_LEAF_UNIT  # page or leaf
                item.statements.append(
                    WikidataStatement(
                        property_id=P_NUMBER_OF_PAGES,
                        value=int(folio_match.group(1)),
                        value_type="quantity",
                        unit=unit_qid,
                        references=ref,
                    )
                )

    def _add_provenance_claims(
        self,
        item: WikidataItem,
        record: dict[str, object],
        ref: list[dict[str, str]],
    ) -> None:
        """Add provenance claims from NER-extracted MARC 561 entities and
        structured ``provenance_events`` (541/583 — Rule W-32 / W-125).

        OWNER → P127 (owned by) with optional P580/P582 date qualifiers.
        Acquisition / exhibition / conservation events → P7153
        (significant place) when a place Wikidata QID is known — never invent
        event-class QIDs for P793. Otherwise retain named-as evidence on P127
        only for ownership agents.
        COLLECTION → noted via P1932 (named as) qualifier on P195.
        """
        entities = record.get("entities") or []
        prov_entities = [e for e in entities if e.get("source") == "provenance_ner"]

        # Promote structured custody events into the same owner/date channels
        # the NER path already understands (Rule W-125).
        for event in record.get("provenance_events") or []:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "").strip().lower()
            agent = str(event.get("agent_name") or "").strip()
            place = str(event.get("place_text") or "").strip()
            year = event.get("year") or event.get("year_earliest")
            if year:
                prov_entities.append({
                    "source": "provenance_ner",
                    "type": "DATE",
                    "text": str(year),
                })
            if agent and event_type in {"acquisition", "ownership", "owner"}:
                role = "former owner" if event_type != "ownership" else "owner"
                prov_entities.append({
                    "source": "provenance_ner",
                    "type": "OWNER",
                    "text": agent,
                    "role": role,
                    "viaf_uri": event.get("viaf_uri"),
                    "mazal_id": event.get("mazal_id"),
                })
            # Significant place from structured custody events (Rule W-125).
            # Avoid inventing unverified event-class QIDs for P793.
            place_qid = str(event.get("wikidata_id") or "").strip()
            if place_qid and not place_qid.upper().startswith("Q"):
                place_qid = f"Q{place_qid}"
            if place_qid and event_type in {
                "exhibition", "conservation", "acquisition", "owner_place",
            }:
                from converter.wikidata.property_mapping import (  # noqa: PLC0415
                    P_SIGNIFICANT_PLACE,
                    P_START_TIME,
                )
                qualifiers: list[dict[str, object]] = []
                if year:
                    try:
                        y = int(str(year)[:4])
                        qualifiers.append({
                            "property": P_START_TIME,
                            "value": f"+{y:04d}-00-00T00:00:00Z",
                            "type": "time",
                        })
                    except ValueError:
                        pass
                if event_type:
                    qualifiers.append({
                        "property": P_OBJECT_NAMED_AS,
                        "value": f"{event_type}: {place}" if place else event_type,
                        "type": "string",
                    })
                elif place:
                    qualifiers.append({
                        "property": P_OBJECT_NAMED_AS,
                        "value": place,
                        "type": "string",
                    })
                item.statements.append(
                    WikidataStatement(
                        property_id=P_SIGNIFICANT_PLACE,
                        value=place_qid,
                        value_type="item",
                        qualifiers=qualifiers,
                        references=ref,
                    )
                )

        if not prov_entities:
            return

        owners = [e for e in prov_entities if e.get("type") == "OWNER"]
        dates = [e for e in prov_entities if e.get("type") == "DATE"]
        collections = [e for e in prov_entities if e.get("type") == "COLLECTION"]

        # Build date qualifiers from DATE entities (P580/P582 per WikiProject Data Model)
        date_qualifiers: list[dict[str, object]] = []
        for date_ent in dates:
            date_text = str(date_ent.get("text", "")).strip().strip(_QUOTE_CHARS + ".:")
            if not date_text:
                continue
            # Try to parse as Gregorian year
            year_match = re.search(r"(\d{4})", date_text)
            if year_match:
                year = int(year_match.group(1))
                date_qualifiers.append(
                    {
                        "property": P_START_TIME,
                        "value": f"+{year:04d}-00-00T00:00:00Z",
                        "type": "time",
                    }
                )

        seen_owners: set[str] = set()
        for owner in owners:
            owner_name = str(owner.get("text", "")).strip().strip(_QUOTE_CHARS + ".")
            owner_role = str(owner.get("role") or "").casefold().replace("_", " ").strip()
            if owner_role in {"former owner", "seller", "censor"}:
                # Historical/sale/censor mentions remain in provenance evidence;
                # P127 is reserved for a supported current ownership assertion.
                continue
            if not owner_name or owner_name in seen_owners:
                continue
            seen_owners.add(owner_name)

            viaf_uri = owner.get("viaf_uri")
            mazal_id = owner.get("mazal_id")
            key = _person_key(owner_name, viaf_uri, mazal_id)

            person_item = self._get_or_create_person(
                owner_name,
                viaf_uri,
                mazal_id,
                "OWNER",
                record,
            )
            resolved_qid = self._person_qids.get(key) or person_item.existing_qid

            # Attach date qualifiers to the ownership statement
            owner_qualifiers = list(date_qualifiers)  # Copy shared dates
            if resolved_qid:
                item.statements.append(
                    WikidataStatement(
                        property_id=P_OWNED_BY,
                        value=resolved_qid,
                        value_type="item",
                        qualifiers=owner_qualifiers,
                        references=ref,
                    )
                )
            else:
                owner_qualifiers.append(
                    {
                        "property": P_OBJECT_NAMED_AS,
                        "value": owner_name.rstrip(",;:"),
                        "type": "string",
                    }
                )
                item.statements.append(
                    WikidataStatement(
                        property_id=P_OWNED_BY,
                        value=f"__LOCAL:{person_item.local_id}",
                        value_type="item",
                        qualifiers=owner_qualifiers,
                        references=ref,
                    )
                )

        seen_colls: set[str] = set()
        for coll in collections:
            coll_name = str(coll.get("text", "")).strip().strip(_QUOTE_CHARS + ".")
            if not coll_name or coll_name in seen_colls:
                continue
            seen_colls.add(coll_name)
            # P195 (collection) expects item QIDs, not strings.
            # NER-extracted collection names are skipped — would need
            # reconciliation to Wikidata institution QIDs.
