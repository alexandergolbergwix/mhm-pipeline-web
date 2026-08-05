"""Manuscript item projection for Wikidata Studio and desktop builds."""

from __future__ import annotations

import re

from converter.wikidata.holding_institutions import institution_qid
from converter.wikidata.item_builder import (
    _QUOTE_CHARS,
    _SOURCE_FILENAME_RE,
    _normalise_label,
    CONDITION_TO_QID,
    P_BASED_ON_HEURISTIC,
    P_CATALOG_CODE,
    P_COLLECTION,
    P_CONDITION,
    P_DESCRIBED_AT_URL,
    P_EARLIEST_DATE,
    P_EXACT_MATCH,
    P_EXEMPLAR_OF,
    P_IIIF_MANIFEST,
    P_INCEPTION,
    P_INSCRIPTION,
    P_INSTANCE_OF,
    P_INVENTORY_NUMBER,
    P_LAST_LINE,
    P_LATEST_DATE,
    P_LOCATION_OF_CREATION,
    P_NLI_CATALOG_ID,
    P_NLI_J9U_ID,
    P_NUMBER_OF_FOLIOS,
    P_NUMBER_OF_PARTS,
    P_OBJECT_HAS_ROLE,
    P_ON_FOCUS_LIST,
    P_SCRIPT_STYLE,
    P_SIGNIFICANT_PLACE,
    P_SOURCING_CIRCUMSTANCES,
    P_TITLE,
    PRECISION_CENTURY,
    PRECISION_YEAR,
    Q_CIRCA,
    Q_COLOPHON,
    Q_CORRECTION,
    Q_GLOSS,
    Q_MANUSCRIPT,
    Q_MARGINALIA,
    Q_WIKIPROJECT_MANUSCRIPTS,
    SCRIPT_TYPE_TO_QID,
    WikidataItem,
    WikidataStatement,
    _associate_item_with_source_record,
    _extract_inception_year,
    _is_catalog_note_placeholder,
    _is_institutional_name,
    _is_printed_facsimile_record,
    _marc_entry_label,
    _normalise_label,
    date_to_wikidata,
    extract_wikidata_qid,
    hmo_wikibase_entity_url,
    hmo_wikibase_page_url,
    known_work_qid_for_title,
    nli_j9u_id,
    nli_reference,
)


_QID_RE = re.compile(r"^Q\d+$")


def _http_url_or_none(value: object) -> str | None:
    """An http(s) URL with MARC quote wrappers stripped, else None."""
    text = str(value or "").strip().strip('"').strip("'").strip()
    return text if text.lower().startswith(("http://", "https://")) else None


def _current_holder_names(record: dict[str, object]) -> list[str]:
    """The attested current-holder names. One extractor, shared with the label.

    Lives in ``item_builder`` so the name that keys ``labels.en`` and the name
    that keys P195 are the same name (Rule W-161) — they used to be produced by
    two functions scanning different fields.
    """
    from converter.wikidata.item_builder import holder_names_from_record  # noqa: PLC0415

    return holder_names_from_record(record)


def _current_holder_qid(record: dict[str, object], holder_names: list[str]) -> str | None:
    """Resolve a current 710 holder to a verified institution QID, if present."""
    wanted = {name.casefold() for name in holder_names}
    for contributor in record.get("contributors") or []:
        if not isinstance(contributor, dict):
            continue
        role = str(contributor.get("role") or "").casefold().replace("_", " ")
        name = _normalise_label(str(contributor.get("name") or ""))
        if "current owner" not in role or name.casefold() not in wanted:
            continue
        # One audited table, not a hand-rolled chain of four (Rule W-143).
        # An institution it cannot resolve unambiguously abstains — it never
        # falls back to NLI (Rule W-75) or guesses (Rule W-84's reasoning).
        resolved = institution_qid(name)
        if resolved:
            return resolved
        for key in ("wikidata_qid", "existing_qid", "qid"):
            qid = extract_wikidata_qid(str(contributor.get(key) or ""))
            if qid and _QID_RE.fullmatch(qid):
                return qid
    for match in record.get("marc_authority_matches") or []:
        if not isinstance(match, dict):
            continue
        role = str(match.get("role") or "").casefold().replace("_", " ")
        name = _normalise_label(str(match.get("name") or match.get("entity_text") or ""))
        if "current owner" not in role or not _is_institutional_name(name):
            continue
        if wanted and name.casefold() not in wanted:
            continue
        qid = extract_wikidata_qid(
            str(match.get("wikidata_qid") or (match.get("payload") or {}).get("wikidata_qid") or "")
        )
        if qid and _QID_RE.fullmatch(qid):
            return qid
    return None


class ManuscriptProjectionMixin:
    def build_manuscript_item(self, record: dict[str, object]) -> WikidataItem:
        """Build a Wikidata item for a single manuscript record."""
        control_number = str(record.get("_control_number", "") or "").strip('"')
        # Explicit ``or ""`` before ``str(...)``: when the record contains an
        # explicit ``title: None`` (rather than the key being absent),
        # ``record.get("title", "")`` returns ``None``, and ``str(None)`` is
        # the four-character string ``"None"`` — which was ending up in the
        # Hebrew label slot and triggering HE_LABEL_IS_LATIN on the validator.
        # Discovered 2026-04-24 via six-NER triage (record 990001330520205171).
        title = str(record.get("title") or "").strip().rstrip(" .,;:/-")
        ref = nli_reference(control_number)

        item = WikidataItem(entity_type="manuscript", local_id=control_number)
        if _is_printed_facsimile_record(record):
            item.semantic_type = "printed_facsimile"
        _associate_item_with_source_record(item, record)

        # ── Labels & descriptions ────────────────────────────────
        self._set_labels(item, record, title)

        # ── Core identity ────────────────────────────────────────
        # Rule 42: emit one P31 per applicable class. When more than one is
        # emitted, the specific QIDs get rank="preferred" so consumers
        # querying wdt:P31 see the most-specific class first; the base
        # Q_MANUSCRIPT keeps the default "normal" rank.
        instance_qids = self._determine_instance_type(record)
        multi = len(instance_qids) > 1
        for qid in instance_qids:
            stmt_rank = "preferred" if multi and qid != Q_MANUSCRIPT else "normal"
            item.statements.append(
                WikidataStatement(
                    property_id=P_INSTANCE_OF,
                    value=qid,
                    value_type="item",
                    references=ref,
                    rank=stmt_rank,
                )
            )

        # Rule 42: P2888 (exact match) bridges the Wikidata item to the
        # project-owned page in mhm-hmo.wikibase.cloud. We deliberately do
        # NOT emit the synthetic HMO graph IRI (record["hmo_iri"]) here —
        # that namespace is for internal TTL traversal and is not hosted
        # on any web server (audit response 2026-05-17). We emit only
        # when a control number is available.
        #
        # Phase 6 (HMO Wikibase Studio, dev-docs/hmo-wikibase-studio-plan.md):
        # once this manuscript has a real Phase 5 upload, point at its
        # actual `/wiki/Item:Q<n>` page. Never emit the dead `/wiki/MS_<cn>`
        # slug or ontology IRI placeholders (Rule W-122).
        wikibase_url = hmo_wikibase_entity_url(
            control_number, self._hmo_instance_qids
        ) or hmo_wikibase_page_url(control_number)
        if wikibase_url:
            item.statements.append(
                WikidataStatement(
                    property_id=P_EXACT_MATCH,
                    value=wikibase_url,
                    value_type="url",
                    references=ref,
                )
            )
            # Rule 44 (Phase 2, 2026-05-17): P973 (described at URL) carries
            # the **direct** wikibase.cloud browse link. Today this is the
            # same URL as P2888, but the two have distinct semantics:
            # P2888 is the academic permalink ("exact match" — survives any
            # hosting migration via the w3id.org redirect once it merges),
            # while P973 always points at the live wikibase.cloud page for
            # humans clicking through. The split becomes meaningful when
            # P2888 switches to https://w3id.org/mhm/manuscript/<cn>.
            item.statements.append(
                WikidataStatement(
                    property_id=P_DESCRIBED_AT_URL,
                    value=wikibase_url,
                    value_type="url",
                    references=ref,
                )
            )

        current_owner_names = _current_holder_names(record)
        collection_qid = _current_holder_qid(record, current_owner_names)
        # Do not infer the NLI collection from the absence of another holder.
        # P195 is a collection claim and requires a verified current-holder
        # QID; the description may still name the catalogued institution.
        if collection_qid:
            item.statements.append(
                WikidataStatement(
                    property_id=P_COLLECTION,
                    value=collection_qid,
                    value_type="item",
                    references=ref,
                    rank="preferred",
                )
            )
        # Do NOT emit P17/P131 country/admin location from catalog institution
        # alone (Rules W-82 / W-75). Holder evidence stays on P195; location
        # claims require explicit geographic evidence, not NLI-catalog default.

        shelfmark = _normalise_label(str(record.get("shelfmark") or ""))
        if shelfmark:
            item.statements.append(
                WikidataStatement(
                    property_id=P_INVENTORY_NUMBER,
                    value=str(shelfmark),
                    value_type="string",
                    # P217 constraint requires P195 (collection) as a qualifier
                    # on the statement itself (not just a top-level P195 claim).
                    # Fix 2026-04-15 third audit Fix #1.
                    qualifiers=(
                        [{"property": P_COLLECTION, "value": collection_qid, "type": "item"}]
                        if collection_qid
                        else []
                    ),
                    references=ref,
                )
            )
        # P3959 (NNL item ID) — the MARC 001 bibliographic control number.
        # This is the direct Wikidata identifier for the NLI catalog record
        # and makes the source MARC record queryable via SPARQL on every item.
        # P3959 accepts 18-digit NLI ALEPH system numbers (990…) as well as the
        # legacy 9-digit format.  It is emitted unconditionally when a control
        # number is available; the J9U / P8189 path below handles AUTHORITY
        # records (9870…) separately.
        if control_number:
            item.statements.append(
                WikidataStatement(
                    property_id=P_NLI_CATALOG_ID,
                    value=control_number,
                    value_type="external-id",
                    references=ref,
                )
            )

        # P8189 (NLI J9U ID) is an AUTHORITY-record identifier — only values
        # with the '9870…' prefix are valid. Manuscript control numbers are
        # bibliographic (prefix '990…'), which do NOT belong on P8189.
        # (Rule 25 bug #2, reinforced by 2026-04-24 six-NER triage where 100/100
        # manuscripts were being blocked by the validator for exactly this.)
        if control_number:
            j9u = nli_j9u_id(control_number)
            if j9u.startswith("9870"):
                item.statements.append(
                    WikidataStatement(
                        property_id=P_NLI_J9U_ID,
                        value=j9u,
                        value_type="external-id",
                        references=ref,
                    )
                )
        if title:
            item.statements.append(
                WikidataStatement(
                    property_id=P_TITLE,
                    value=_normalise_label(title),
                    value_type="monolingualtext",
                    language="he",
                    references=ref,
                )
            )
        subtitle = str(
            record.get("subtitle")
            or record.get("245$b")
            or ""
        ).strip().strip(_QUOTE_CHARS + " /:")
        if subtitle:
            from converter.wikidata.property_mapping import P_SUBTITLE  # noqa: PLC0415

            item.statements.append(
                WikidataStatement(
                    property_id=P_SUBTITLE,
                    value=_normalise_label(subtitle),
                    value_type="monolingualtext",
                    language="he",
                    references=ref,
                )
            )

        # ── Language & writing system ────────────────────────────
        self._add_languages(item, record, ref)

        # ── Script type (paleographic) ───────────────────────────
        script_type = record.get("script_type")
        if script_type and str(script_type) in SCRIPT_TYPE_TO_QID:
            item.statements.append(
                WikidataStatement(
                    property_id=P_SCRIPT_STYLE,
                    value=SCRIPT_TYPE_TO_QID[str(script_type)],
                    value_type="item",
                    references=ref,
                )
            )

        # ── Dates ────────────────────────────────────────────────
        colophon_fields = set(record.get("data_from_colophon") or [])
        dates = record.get("dates") or {}
        date_result = date_to_wikidata(dates)
        if date_result:
            time_value, precision, calendarmodel, earliest_year, latest_year = date_result
            # Add P1480 (circa) qualifier when date certainty is not exact
            qualifiers: list[dict[str, object]] = []
            cert_levels = record.get("certainty_levels") or {}
            date_cert = cert_levels.get("date", "")
            if date_cert and date_cert != "Certain":
                qualifiers.append(
                    {
                        "property": P_SOURCING_CIRCUMSTANCES,
                        "value": Q_CIRCA,
                        "type": "item",
                    }
                )
            # Fix 2026-04-15 third audit Fix #12: century-precision dates lack
            # explicit start/end bounds. Add P1319 (earliest date) and P1326
            # (latest date) qualifiers so reviewers can understand the full range
            # rather than mistaking the century-start year for an exact date.
            if precision == PRECISION_CENTURY and earliest_year and latest_year:
                qualifiers.append(
                    {
                        "property": P_EARLIEST_DATE,
                        "value": f"+{earliest_year:04d}-00-00T00:00:00Z",
                        "type": "time",
                        "precision": PRECISION_YEAR,
                    }
                )
                qualifiers.append(
                    {
                        "property": P_LATEST_DATE,
                        "value": f"+{latest_year:04d}-00-00T00:00:00Z",
                        "type": "time",
                        "precision": PRECISION_YEAR,
                    }
                )
            # P887 (based on heuristic) = colophon when date is from colophon.
            # P887's property-scope constraint is "as reference" only — it must
            # NOT be used as a statement qualifier. Move it into the reference
            # block alongside P248/P813. Fix 2026-04-15 third audit Fix #3.
            if "dates" in colophon_fields or "colophon_text" in colophon_fields:
                colophon_ref = list(ref) + [
                    {
                        "property": P_BASED_ON_HEURISTIC,
                        "value": Q_COLOPHON,
                        "type": "item",
                    }
                ]
                inception_ref = colophon_ref
            else:
                inception_ref = ref
            item.statements.append(
                WikidataStatement(
                    property_id=P_INCEPTION,
                    value=time_value,
                    value_type="time",
                    precision=precision,
                    qualifiers=qualifiers,
                    references=inception_ref,
                )
            )

        # ── Location of creation (KIMA places → Wikidata QIDs) ──
        kima_places = record.get("kima_places") or {}
        for _place_name, wikidata_uri in kima_places.items():
            qid = extract_wikidata_qid(str(wikidata_uri))
            if qid:
                item.statements.append(
                    WikidataStatement(
                        property_id=P_LOCATION_OF_CREATION,
                        value=qid,
                        value_type="item",
                        references=ref,
                    )
                )

        # ── Physical description ─────────────────────────────────
        self._add_physical_description(item, record, ref)

        # ── Digital access ───────────────────────────────────────
        # 856$u reaches us quote-wrapped and occasionally holds prose rather
        # than a link; a url-typed claim must be a real URL (Rule W-140).
        digital_url = _http_url_or_none(record.get("digital_url"))
        if digital_url:
            item.statements.append(
                WikidataStatement(
                    property_id=P_DESCRIBED_AT_URL,
                    value=str(digital_url),
                    value_type="url",
                    references=ref,
                )
            )
        # Rule 45 P6108 coexistence (2026-05-18): the two manifests carry
        # different payloads — NLI's manifest (from MARC 856) hosts the
        # real high-resolution Canvas images, while ours (published to
        # mhm-hmo.wikibase.cloud by Stage 6.5) carries the HMO scholarly
        # overlay (Codicological_Unit Ranges, ScribalIntervention /
        # Colophon / Marginalia AnnotationCollections, seeAlso to the
        # HMO graph node) on placeholder Canvases. Both must be reachable
        # from Wikidata or the consumer loses one of the two views.
        #
        # Rank semantics: when both URLs exist, NLI's image-rich manifest
        # is ranked "preferred" so image-only IIIF consumers pick it by
        # default; our overlay manifest stays at "normal" rank and
        # remains discoverable via P6108 (see Rule 45 sub-section
        # "P6108 coexistence"). When only one URL is present, it is
        # emitted at "normal" rank (current behaviour preserved).
        nli_iiif_url = record.get("iiif_manifest_url")
        published_iiif_url = record.get("iiif_manifest_published_url")
        both_present = bool(nli_iiif_url) and bool(published_iiif_url)
        if nli_iiif_url:
            item.statements.append(
                WikidataStatement(
                    property_id=P_IIIF_MANIFEST,
                    value=str(nli_iiif_url),
                    value_type="url",
                    references=ref,
                    rank="preferred" if both_present else "normal",
                )
            )
        if published_iiif_url:
            item.statements.append(
                WikidataStatement(
                    property_id=P_IIIF_MANIFEST,
                    value=str(published_iiif_url),
                    value_type="url",
                    references=ref,
                    rank="normal",
                )
            )

        # ── Genres (MARC 655) ────────────────────────────────────
        self._add_marc_genres(item, record, ref, title)

        # ── Subjects (MARC 650 + canonical refs) → P921 ─────────
        self._add_canonical_subjects(item, record, ref)

        # ── Contents / works (P1574 exemplar of) ────────────────
        self._add_contents(item, record, ref)

        # ── Incipit (first line of text) → P1922 ───────────────
        incipit = record.get("has_incipit")
        if incipit and str(incipit).strip() and str(incipit) != "None":
            from converter.wikidata.property_mapping import P_FIRST_LINE  # noqa: PLC0415

            item.statements.append(
                WikidataStatement(
                    property_id=P_FIRST_LINE,
                    value=str(incipit).strip().strip(_QUOTE_CHARS),
                    value_type="monolingualtext",
                    language="he",
                    references=ref,
                )
            )

        # ── Explicit (last line of text) → P3132 ───────────────
        explicit = record.get("has_explicit")
        if explicit and str(explicit).strip() and str(explicit) != "None":
            item.statements.append(
                WikidataStatement(
                    property_id=P_LAST_LINE,
                    value=str(explicit).strip().strip(_QUOTE_CHARS),
                    value_type="monolingualtext",
                    language="he",
                    references=ref,
                )
            )

        # ── Condition → P5816 (keyword → QID mapping + date parsing)
        for cond_note in record.get("condition_notes") or []:
            cond_text = str(cond_note).strip()
            # Try keyword → QID
            matched = False
            for keyword, qid in CONDITION_TO_QID.items():
                if keyword in cond_text.lower():
                    item.statements.append(
                        WikidataStatement(
                            property_id=P_CONDITION,
                            value=qid,
                            value_type="item",
                            references=ref,
                        )
                    )
                    matched = True
                    break
            if matched:
                continue
            # Try YYYYMMDD date → restoration date
            date_match = re.match(r"(\d{4})(\d{2})(\d{2})", cond_text)
            if date_match:
                y, m, d = date_match.groups()
                from converter.wikidata.property_mapping import Q_RESTORED  # noqa: PLC0415

                item.statements.append(
                    WikidataStatement(
                        property_id=P_CONDITION,
                        value=Q_RESTORED,
                        value_type="item",
                        references=ref,
                        qualifiers=[
                            {
                                "property": "P585",
                                "value": f"+{y}-{m}-{d}T00:00:00Z",
                                "type": "time",
                            }
                        ],
                    )
                )

        # ── Catalog references ───────────────────────────────────
        for cat_ref in record.get("catalog_references") or []:
            cat_name = cat_ref.get("catalog", "") if isinstance(cat_ref, dict) else str(cat_ref)
            if cat_name:
                item.statements.append(
                    WikidataStatement(
                        property_id=P_CATALOG_CODE,
                        value=str(cat_name),
                        value_type="string",
                        references=ref,
                    )
                )

        # ── Summary (MARC 520) → P7535 ─────────────────────────
        summary = record.get("summary")
        if (
            summary
            and not _is_catalog_note_placeholder(summary)
            and str(summary).strip()
            and str(summary) != "None"
        ):
            item.statements.append(
                WikidataStatement(
                    property_id="P7535",
                    value=str(summary),
                    value_type="monolingualtext",
                    language="he",
                    references=ref,
                )
            )

        # ── Rights (MARC 540) → P6216 ───────────────────────────
        # Historical Hebrew manuscripts are public domain (pre-1900 works).
        # Rights statements from NLI describe digital copy access, not copyright.
        # Bug fix 2026-04-16 (deeper audit Fix #15): only assert public-domain
        # status when the inception date is known AND before 1900. A 20th-c.
        # manuscript could otherwise receive an incorrect public-domain claim.
        rights = record.get("rights_statement")
        if rights and str(rights).strip() and str(rights) != "None":
            inception_year = _extract_inception_year(record)
            if inception_year is not None and inception_year < 1900:
                item.statements.append(
                    WikidataStatement(
                        property_id="P6216",
                        value="Q19652",
                        value_type="item",
                        # Fix 2026-04-15 third audit Fix #11: P6216 without a
                        # jurisdiction qualifier asserts *universal* public domain,
                        # which is legally incorrect. Scope to Israel (Q801) under
                        # Israeli copyright law.
                        qualifiers=[
                            {
                                "property": "P1001",
                                "value": "Q801",
                                "type": "item",
                            }
                        ],
                        references=ref,
                    )
                )

        # ── Person claims (authors, scribes, owners from MARC + NER) ──
        self._add_person_claims(item, record, ref)

        # ── Provenance claims (owners from NER on MARC 561) ─────
        self._add_provenance_claims(item, record, ref)

        # ── Number of codicological units → P2635 ───────────────
        codic_units = record.get("codicological_units") or []
        if len(codic_units) > 1:
            item.statements.append(
                WikidataStatement(
                    property_id=P_NUMBER_OF_PARTS,
                    value=len(codic_units),
                    value_type="quantity",
                    references=ref,
                )
            )

        # ── Colophon text → P1684 (inscription) ─────────────────
        colophon = record.get("colophon_text")
        if (
            colophon
            and not _is_catalog_note_placeholder(colophon)
            and str(colophon).strip()
            and str(colophon) != "None"
        ):
            colophon_qualifiers: list[dict[str, object]] = [
                {
                    "property": P_OBJECT_HAS_ROLE,
                    "value": Q_COLOPHON,
                    "type": "item",
                }
            ]
            # Rule 42: when upstream (NER post-filter or HMO TextLocation)
            # surfaces a folio reference for the colophon, attach it as
            # P7416. Never invent folio data when absent.
            colophon_folio = record.get("colophon_folio")
            if colophon_folio and str(colophon_folio).strip():
                colophon_qualifiers.append(
                    {
                        "property": P_NUMBER_OF_FOLIOS,
                        "value": str(colophon_folio).strip(),
                        "type": "string",
                    }
                )
            item.statements.append(
                WikidataStatement(
                    property_id=P_INSCRIPTION,
                    value=str(colophon).strip()[:1500],
                    value_type="monolingualtext",
                    language="he",
                    references=ref,
                    qualifiers=colophon_qualifiers,
                )
            )

        # ── Scribal interventions → P1684 (inscription) ─────────
        for intervention in record.get("scribal_interventions") or []:
            text = (
                str(intervention.get("text", "")).strip()
                if isinstance(intervention, dict)
                else str(intervention).strip()
            )
            if not text or text == "None" or _is_catalog_note_placeholder(text):
                continue
            int_type = (
                str(intervention.get("type", "")).lower() if isinstance(intervention, dict) else ""
            )
            role_qid = (
                Q_GLOSS
                if "gloss" in int_type
                else Q_CORRECTION
                if "correct" in int_type
                else Q_MARGINALIA
            )
            intervention_qualifiers: list[dict[str, object]] = [
                {
                    "property": P_OBJECT_HAS_ROLE,
                    "value": role_qid,
                    "type": "item",
                }
            ]
            # Rule 42: same conditional folio wiring as colophon above.
            intervention_folio = (
                intervention.get("folio") if isinstance(intervention, dict) else None
            )
            if intervention_folio and str(intervention_folio).strip():
                intervention_qualifiers.append(
                    {
                        "property": P_NUMBER_OF_FOLIOS,
                        "value": str(intervention_folio).strip(),
                        "type": "string",
                    }
                )
            item.statements.append(
                WikidataStatement(
                    property_id=P_INSCRIPTION,
                    value=text[:1500],
                    value_type="monolingualtext",
                    language="he",
                    references=ref,
                    qualifiers=intervention_qualifiers,
                )
            )

        # ── Multi-volume: emit P2635 if volume count > CU count ────
        # P478 (volume in series) is for "Vol. 3 of set X" labels — NOT for
        # "total number of volumes = 4". When volume_info contains a total
        # count ("4 כרכים ;"), parse the number and emit P2635 only if it
        # differs from the CU-based P2635 already emitted above.
        vol_info = record.get("volume_info")
        if vol_info and str(vol_info).strip() and str(vol_info) != "None":
            vol_text = str(vol_info).strip().rstrip(";., ")
            _vol_count_match = re.search(r"\d+", vol_text)
            if _vol_count_match:
                vol_count = int(_vol_count_match.group())
                cu_count = len(record.get("codicological_units") or [])
                # Emit P2635 only when volume count is informative and
                # different from the already-emitted CU-based count.
                if vol_count > 1 and vol_count != cu_count:
                    item.statements.append(
                        WikidataStatement(
                            property_id=P_NUMBER_OF_PARTS,
                            value=vol_count,
                            value_type="quantity",
                            references=ref,
                        )
                    )

        # ── Related places → P7153 (significant place) ──────────
        for place_entry in record.get("related_places") or []:
            place_name = _marc_entry_label(place_entry, keys=("place", "name", "term"))
            if not place_name:
                continue
            for _name, uri in (record.get("kima_places") or {}).items():
                if place_name in _name or _name in place_name:
                    qid = extract_wikidata_qid(str(uri))
                    if qid:
                        item.statements.append(
                            WikidataStatement(
                                property_id=P_SIGNIFICANT_PLACE,
                                value=qid,
                                value_type="item",
                                # P7153 constraint requires P3831 (object of statement
                                # has role) qualifier. Use Q1773840 (provenance) as the
                                # role — the closest concept for "place associated with
                                # the manuscript's custody history". Bug fix 2026-04-16:
                                # Q1616923 was previously used but is a disambiguation
                                # page (Heydeck), not a role concept.
                                qualifiers=[
                                    {
                                        "property": P_OBJECT_HAS_ROLE,
                                        "value": "Q1773840",
                                        "type": "item",
                                    }
                                ],
                                references=ref,
                            )
                        )
                        break

        # ── General notes (MARC 500) → P7535 ────────────────────
        for note in record.get("notes") or []:
            note_text = str(note).strip()
            if _is_catalog_note_placeholder(note_text):
                continue
            if not note_text or note_text == "None" or len(note_text) <= 5:
                continue
            if _SOURCE_FILENAME_RE.match(note_text):
                continue  # skip internal NLI catalog filenames (MARC 500 artifact)
            item.statements.append(
                WikidataStatement(
                    property_id="P7535",
                    value=note_text[:1500],
                    value_type="monolingualtext",
                    language="he",
                    references=ref,
                )
            )

        # ── Provenance raw text (MARC 561) → P7535 + provenance qualifier
        prov_text = record.get("provenance")
        if (
            prov_text
            and not _is_catalog_note_placeholder(prov_text)
            and str(prov_text).strip()
            and str(prov_text) != "None"
        ):
            item.statements.append(
                WikidataStatement(
                    property_id="P7535",
                    value=str(prov_text).strip()[:1500],
                    value_type="monolingualtext",
                    language="he",
                    references=ref,
                )
            )

        # ── Multiple scribal hands → P7535 note ─────────────────
        if record.get("has_multiple_hands"):
            item.statements.append(
                WikidataStatement(
                    property_id="P7535",
                    value="Written in multiple scribal hands",
                    value_type="monolingualtext",
                    language="en",
                    references=ref,
                )
            )

        # ── Related works → P1574 only with accepted evidence (W-68 / W-114)
        # Bare related_works titles must not mint local CREATE works without
        # MARC 505/500 / approved NER / authority / known-QID evidence — that
        # path produced WORK_WITHOUT_SOURCE_EVIDENCE and blocked Studio rebuild.
        from converter.wikidata.work_candidates import assess_work_candidate  # noqa: PLC0415

        for rw in record.get("related_works") or []:
            rw_dict = rw if isinstance(rw, dict) else {"title": str(rw)}
            rw_title = str(rw_dict.get("title") or "").strip().strip(_QUOTE_CHARS + ".")
            if not rw_title:
                continue
            source_field = str(
                rw_dict.get("source_field") or rw_dict.get("field") or "related_works"
            ).strip() or "related_works"
            explicit_qid = str(
                rw_dict.get("wikidata_qid")
                or rw_dict.get("wikidata_id")
                or "",
            ).strip()
            if not _QID_RE.match(explicit_qid):
                explicit_qid = ""
            rw_qid = explicit_qid or known_work_qid_for_title(rw_title)
            approved = bool(rw_dict.get("approved")) or bool(rw_qid)
            decision = assess_work_candidate(
                rw_title,
                source_field=source_field,
                approved=approved,
                known_qid=rw_qid or None,
                candidate_kind=rw_dict.get("candidate_kind") or "related_work",
                source_text=str(rw_dict.get("source_text") or rw_title),
            )
            evidence = decision.evidence()
            if evidence not in item.work_candidate_evidence:
                item.work_candidate_evidence.append(evidence)
            if not decision.accepted or not decision.title:
                continue
            if rw_qid:
                item.statements.append(
                    WikidataStatement(
                        property_id=P_EXEMPLAR_OF,
                        value=rw_qid,
                        value_type="item",
                        references=ref,
                    )
                )
                continue
            # Accepted without a known QID only when curator-approved on the
            # related-work row — still stamp evidence on the local work.
            rw_item = self._get_or_create_work(decision.title, None, record)
            if evidence not in rw_item.work_candidate_evidence:
                rw_item.work_candidate_evidence.append(evidence)
            item.statements.append(
                WikidataStatement(
                    property_id=P_EXEMPLAR_OF,
                    value=f"__LOCAL:{rw_item.local_id}",
                    value_type="item",
                    references=ref,
                )
            )

        # ── WikiProject Manuscripts ──────────────────────────────
        item.statements.append(
            WikidataStatement(
                property_id=P_ON_FOCUS_LIST,
                value=Q_WIKIPROJECT_MANUSCRIPTS,
                value_type="item",
            )
        )

        # P7535 is scoped to archival collections, not arbitrary manuscript
        # catalog notes; P5008 is administrative and adds no notability.
        # P17/P131 must never be reintroduced without explicit geo evidence.
        item.statements = [
            statement
            for statement in item.statements
            if statement.property_id not in {"P7535", "P5008", "P17", "P131", "P50"}
        ]

        return item
