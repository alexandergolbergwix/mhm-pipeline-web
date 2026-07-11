"""Person item construction and manuscript-person linking."""

from __future__ import annotations

import re

from converter.wikidata.item_builder import (
    LANG_TO_QID,
    PRECISION_YEAR,
    P_AUTHOR,
    P_DATE_OF_BIRTH,
    P_DATE_OF_DEATH,
    P_INSTANCE_OF,
    P_NATURE_OF_STATEMENT,
    P_NLI_J9U_ID,
    P_OBJECT_HAS_ROLE,
    P_OBJECT_NAMED_AS,
    P_OCCUPATION,
    P_SOURCING_CIRCUMSTANCES,
    P_VIAF_ID,
    Q_AUTHOR_OCCUPATION,
    Q_HUMAN,
    Q_HYPOTHESIS,
    Q_NLI,
    Q_ORGANIZATION,
    Q_PRESUMABLY,
    ROLE_TO_PID,
    WikidataItem,
    WikidataStatement,
    _ROLE_TO_OCCUPATION,
    _associate_item_with_source_record,
    _build_person_description,
    _has_hebrew_script,
    _is_anonymous_name,
    _is_institutional_name,
    _is_role_descriptor,
    _normalise_label,
    _person_key,
    _strip_name_quotes,
    _strip_person_name_qualifiers,
    _to_natural_name_order,
    extract_viaf_id,
    logger,
    nli_authority_reference,
    nli_reference,
    viaf_reference,
)


class PersonProjectionMixin:
    def _get_or_create_person(
        self,
        name: str,
        viaf_uri: str | None,
        mazal_id: str | None,
        role: str,
        source_record: dict[str, object],
    ) -> WikidataItem:
        """Get existing or create new person item with full authority data."""
        key = _person_key(name, viaf_uri, mazal_id)
        if key in self._person_items:
            person = self._person_items[key]
            _associate_item_with_source_record(person, source_record)
            return person
        if key in self._skipped_person_keys:
            # Same skip decision already made; return the cached stub so
            # callers can still inspect .labels==() to route to P2093,
            # but don't re-emit the warning.
            return self._skipped_person_stubs[key]

        person = WikidataItem(entity_type="person", local_id=key)
        _associate_item_with_source_record(person, source_record)

        # Clean name: strip surrounding quotes (ASCII + Unicode typographic)
        # and trailing MARC ISBD punctuation.  The full set ",;:." matches the
        # validator's TRAILING_PUNCTUATION rule — missing the period here let
        # "בחיי בן יוסף אבן פקודה." through unfiltered.
        clean_name = _strip_name_quotes(name)
        if not clean_name or len(clean_name) < 2:
            # Stub, but do NOT add to _person_items — see _skipped_person_keys
            self._skipped_person_keys.add(key)
            self._skipped_person_stubs[key] = person
            return person

        # Fix 2026-04-15 third audit Fix #5: skip generic anonymous placeholders.
        if _is_anonymous_name(clean_name):
            logger.warning("Skipping anonymous/placeholder person name: %r", clean_name)
            self._skipped_person_keys.add(key)
            self._skipped_person_stubs[key] = person
            return person

        # Skip names that are role-descriptors or bare place-names — these are
        # MARC catalog conventions for unnamed persons ("apostate", "from Salonika")
        # and must never become Wikidata item labels.  Root cause of the
        # "משומד"/"שאלוניקי" deletion batch (2026-04-20).
        if _is_role_descriptor(clean_name):
            logger.warning(
                "Skipping role-descriptor/place-name used as person identifier: %r",
                clean_name,
            )
            self._skipped_person_keys.add(key)
            self._skipped_person_stubs[key] = person
            return person

        # Fix 2026-04-15 third audit Fix #4: Wikidata:Notability requires person
        # items to have at least one external identifier (VIAF, NLI J9U, LCCN,
        # GND, ISNI, BnF, or Wikidata QID). Creating items with only a name and
        # no identifiers invites mass deletion requests from the community. Skip
        # the item and let callers fall back to P2093 (author name string).
        match_info: dict[str, object] = {}
        for m in source_record.get("marc_authority_matches") or []:
            mid = str(m.get("mazal_id", ""))
            vid = str(m.get("viaf_uri", ""))
            if (mazal_id and mid == mazal_id) or (viaf_uri and vid == viaf_uri):
                match_info = m  # type: ignore[assignment]
                break
        # Fallback: when no authority-ID match was found (neither viaf_uri nor
        # mazal_id set), search by name prefix — catches persons resolved via
        # direct Wikidata QID lookup rather than VIAF/Mazal.
        if not match_info:
            name_prefix = clean_name[:6] if len(clean_name) >= 6 else clean_name
            for m in source_record.get("marc_authority_matches") or []:
                entry_name = str(m.get("name", "") or "")
                if entry_name and entry_name[: len(name_prefix)] == name_prefix:
                    match_info = m  # type: ignore[assignment]
                    break
        name_type = (
            str(match_info.get("name_type") or match_info.get("viaf_name_type") or "")
            .strip()
            .lower()
        )
        if name_type and name_type not in {"personal", "person", "individual"}:
            logger.warning(
                "Skipping non-person authority %r with name_type=%r",
                clean_name,
                name_type,
            )
            self._skipped_person_keys.add(key)
            self._skipped_person_stubs[key] = person
            return person
        person.authority_evidence = (
            [
                {
                    key: value
                    for key, value in {
                        "source": match_info.get("source"),
                        "name_type": name_type or "Personal",
                        "viaf_uri": viaf_uri,
                        "mazal_id": mazal_id,
                        "wikidata_qid": match_info.get("wikidata_qid"),
                        "birth_year": match_info.get("birth_year"),
                        "death_year": match_info.get("death_year"),
                        "dates": match_info.get("dates"),
                        "preferred_name_lat": match_info.get("preferred_name_lat"),
                        "preferred_name_heb": match_info.get("preferred_name_heb"),
                    }.items()
                    if value not in (None, "", [], {})
                }
            ]
            if match_info or viaf_uri or mazal_id
            else []
        )
        has_identifier = any(
            [
                viaf_uri,
                mazal_id,
                match_info.get("gnd_id"),
                match_info.get("lc_id"),
                match_info.get("isni"),
                match_info.get("bnf_id"),
                # A known Wikidata QID is the strongest possible notability
                # signal — the person already exists in Wikidata.
                match_info.get("wikidata_qid"),
            ]
        )
        if not has_identifier:
            logger.warning(
                "Skipping person %r — no external identifier (Wikidata:Notability). "
                "Callers should use P2093 (author name string) instead.",
                clean_name,
            )
            # Return the stub (so the caller's `if not person_item.labels`
            # branch fires and routes to P2093) but do NOT persist it in
            # ``_person_items`` — otherwise 251 empty-label items surface in
            # build_all's output and get rejected as NO_IDENTIFIER/EMPTY_LABEL.
            self._skipped_person_keys.add(key)
            self._skipped_person_stubs[key] = person
            return person

        # When a Wikidata QID is known from authority matching (direct QID
        # lookup, not via VIAF/Mazal), pre-seed existing_qid so the manuscript
        # claim uses the real Q-number and the person item gets UPDATE semantics
        # in QuickStatements (not CREATE).
        pre_known_qid = str(match_info.get("wikidata_qid") or "")
        if pre_known_qid and not person.existing_qid:
            person.existing_qid = pre_known_qid

        # Label-based deduplication: search Wikidata for an existing human with
        # the same Hebrew/Latin label before creating a new item.  This catches
        # persons who exist in Wikidata without any of our identifier properties
        # (VIAF/NLI/LCCN not yet recorded there), preventing duplicate items.
        # Only fires when the reconciler has not already found a QID via identifiers.
        if self._reconciler is not None and not person.existing_qid:
            existing_qid = self._reconciler.reconcile_person_by_label(clean_name)
            if isinstance(existing_qid, str) and existing_qid:
                person.existing_qid = existing_qid
                logger.info(
                    "Label-based dedup: %r matched existing Wikidata item %s",
                    clean_name,
                    existing_qid,
                )

        # Bug fix 2026-04-16 (deeper audit Fix #1): every person statement
        # must carry a P248 reference. Use VIAF cluster URL when the person
        # has a VIAF ID; otherwise fall back to the parent manuscript's
        # NLI catalog URL where the name was first encountered. Without
        # references, WikiProject Authority Control flags the bot at WD:AN.
        viaf_id_for_ref = extract_viaf_id(str(viaf_uri)) if viaf_uri else None
        if viaf_id_for_ref:
            person_ref = viaf_reference(viaf_id_for_ref)
        else:
            authority_id = str(mazal_id or match_info.get("j9u_id") or "").strip()
            if authority_id.startswith("9870"):
                person_ref = nli_authority_reference(authority_id)
            else:
                ms_ctrl = str(source_record.get("_control_number") or "")
                person_ref = nli_reference(ms_ctrl) if ms_ctrl else []

        # Detect script: Hebrew vs Latin
        has_hebrew = any("\u0590" <= c <= "\u05ff" for c in clean_name)
        label_lang = "he" if has_hebrew else "en"
        # Wikidata convention is "Given Surname" (natural order), NOT MARC's
        # inverted "Surname, Given" form. Bug fix (2026-04-15, Geagea complaint
        # on Q139230386 where label was "סופינו, עמנואל"): flip inverted forms
        # to natural order for the LABEL. The original inverted form is
        # preserved in P1559 (native name) below for searchability.
        person.labels[label_lang] = _normalise_label(
            _strip_person_name_qualifiers(_to_natural_name_order(clean_name))
        )

        pref_lat = str(match_info.get("preferred_name_lat") or "").strip()
        if pref_lat:
            normalized_lat = _normalise_label(
                _strip_person_name_qualifiers(_to_natural_name_order(pref_lat))
            )
            if _has_hebrew_script(normalized_lat) and not re.search(r"[A-Za-z]", normalized_lat):
                if "he" not in person.labels:
                    person.labels["he"] = normalized_lat
            else:
                person.labels["en"] = normalized_lat

        pref_heb = str(match_info.get("preferred_name_heb") or "").strip()
        if pref_heb:
            person.labels["he"] = _normalise_label(
                _strip_person_name_qualifiers(_to_natural_name_order(pref_heb))
            )

        # P31 = human (or organization) — uses the shared institutional
        # keyword list (see _is_institutional_name above).
        is_org = _is_institutional_name(name)
        person.statements.append(
            WikidataStatement(
                property_id=P_INSTANCE_OF,
                value=Q_ORGANIZATION if is_org else Q_HUMAN,
                value_type="item",
            )
        )

        # Extract dates: first try direct authority ID match, then name match
        birth_year = None
        death_year = None
        dates_str = ""

        # Strategy 1: Match by authority ID (most reliable)
        for match in source_record.get("marc_authority_matches") or []:
            mid = str(match.get("mazal_id", ""))
            vid = str(match.get("viaf_uri", ""))
            if (mazal_id and mid == mazal_id) or (viaf_uri and vid == viaf_uri):
                birth_year = match.get("birth_year")
                death_year = match.get("death_year")
                dates_str = str(match.get("dates", ""))
                break

        # Strategy 2: Name matching across all sources
        if not birth_year and not death_year:
            for person_list in [
                source_record.get("authors") or [],
                source_record.get("contributors") or [],
                source_record.get("marc_authority_matches") or [],
            ]:
                for entry in person_list:
                    entry_name = str(entry.get("name", ""))
                    if entry_name and name and entry_name[:5] == name[:5]:
                        birth_year = entry.get("birth_year")
                        death_year = entry.get("death_year")
                        dates_str = str(entry.get("dates", ""))
                        break
                if birth_year or death_year:
                    break

        # Strategy 3: Parse dates string if we have it but not individual years
        if not birth_year and not death_year and dates_str and dates_str != "None":
            parts = re.split(r"[-–]", dates_str.strip())
            for p in parts:
                p = p.strip()
                if p and p.isdigit():
                    yr = int(p)
                    if 100 < yr < 2100:
                        if birth_year is None:
                            birth_year = yr
                        else:
                            death_year = yr

        if birth_year is not None and death_year is not None:
            try:
                birth_int = int(birth_year)
                death_int = int(death_year)
            except (TypeError, ValueError):
                birth_int = death_int = 0
            if death_int < birth_int or death_int - birth_int > 130:
                logger.warning(
                    "Dropping contradictory authority dates for %r: %r-%r",
                    clean_name,
                    birth_year,
                    death_year,
                )
                birth_year = death_year = None

        # Bug fix 2026-04-16 (deeper audit Fix #13): person descriptions
        # should disambiguate, not just restate dates. Build as
        # "<role> (<dates>)" when role is known. Falls back gracefully.
        person.descriptions["en"] = _build_person_description(
            role=role,
            dates_str=dates_str,
            is_org=is_org,
        )

        # P569/P570 = birth/death dates
        if birth_year and not is_org:
            person.statements.append(
                WikidataStatement(
                    property_id=P_DATE_OF_BIRTH,
                    value=f"+{int(birth_year):04d}-00-00T00:00:00Z",
                    value_type="time",
                    precision=PRECISION_YEAR,
                )
            )
        if death_year and not is_org:
            person.statements.append(
                WikidataStatement(
                    property_id=P_DATE_OF_DEATH,
                    value=f"+{int(death_year):04d}-00-00T00:00:00Z",
                    value_type="time",
                    precision=PRECISION_YEAR,
                )
            )

        # P106 = occupation (from role)
        occupation_qid = _ROLE_TO_OCCUPATION.get(role)
        if occupation_qid and not is_org:
            person.statements.append(
                WikidataStatement(
                    property_id=P_OCCUPATION,
                    value=occupation_qid,
                    value_type="item",
                )
            )

        # P214 = VIAF ID (critical for LOD linking)
        # Guard 1: never assign VIAF to an organisation item.
        # Guard 2: reject non-Personal VIAF clusters (nameType cross-validation).
        #   Root cause of "שאלוניקי" incident (2026-04-20): VIAF cluster 76186581
        #   is a Geographic cluster for Salonika; it was attached to person items
        #   because match_info["name_type"] was never checked here.
        #   Root cause of "משומד" incident: VIAF 11810679 (Domenico Gerosolimitano,
        #   the manuscript censor) was attached to unnamed apostates because the
        #   authority matcher found the censor's VIAF in the same manuscript.
        viaf_id = extract_viaf_id(str(viaf_uri)) if viaf_uri else None
        viaf_name_type = str(match_info.get("name_type") or "")
        if viaf_name_type and viaf_name_type != "Personal":
            logger.warning(
                "Skipping VIAF %s for %r — cluster nameType=%r is not Personal",
                viaf_id,
                clean_name,
                viaf_name_type,
            )
            viaf_id = None
        if viaf_id and not is_org:
            person.statements.append(
                WikidataStatement(
                    property_id=P_VIAF_ID,
                    value=viaf_id,
                    value_type="external-id",
                )
            )

        # P8189 = NLI J9U ID — STRICT: only attach when ALL three are true:
        #   1. The ID exists (mazal_id is non-empty)
        #   2. The ID has the AUTHORITY-record prefix '9870…' (NOT bibliographic '990…')
        #   3. The target item is a person, not an organisation
        # Bug fix (2026-04-15, Geagea complaint): bibliographic record IDs were
        # being attached to person items, causing the Property talk:P8189
        # /Duplicates/humans page to flood with false-positive duplicates.
        # P8189's format URL is nli.org.il/en/authorities/$1 — authority-only.
        mazal_str = str(mazal_id) if mazal_id else ""
        if mazal_str and mazal_str.startswith("9870") and not is_org:
            person.statements.append(
                WikidataStatement(
                    property_id=P_NLI_J9U_ID,
                    value=mazal_str,
                    value_type="external-id",
                )
            )
        elif mazal_str and not mazal_str.startswith("9870"):
            # Bibliographic ID (990…) or unknown prefix — do NOT attach P8189.
            # Could be logged for offline review.
            pass

        j9u_from_viaf = str(match_info.get("j9u_id") or "").strip()
        if j9u_from_viaf and j9u_from_viaf.startswith("987") and not is_org:
            if not (mazal_str and mazal_str.startswith("9870")):
                person.statements.append(
                    WikidataStatement(
                        property_id=P_NLI_J9U_ID,
                        value=j9u_from_viaf,
                        value_type="external-id",
                    )
                )

        # P21 (sex or gender): intentionally NOT set.
        # Bug fix 2026-04-15 (web audit): the MARC source records carry no
        # reliable gender information, and unsourced bot-added gender claims
        # are flagged by the community (UW iSchool 2023 "P21 Problem" study).
        # For medieval scribes, gender is often genuinely unknown. Future
        # enrichment may derive P21 from MARC 375 if/when it is populated by
        # the cataloger. Until then, omit P21 entirely rather than asserting
        # a default that is unsourced and may be wrong.

        # P1343 = described by source: intentionally NOT emitted as a main statement.
        # Fix 2026-04-15 third audit Fix #10: P1343 is for publications that
        # *describe* a subject (encyclopedias, biographies). Ktiv (Q118384267)
        # is a library catalog — not a descriptive publication. The NLI catalog
        # reference is already recorded as a reference snak (P248=Q118384267)
        # on every statement via nli_reference() / viaf_reference(), so the
        # provenance is not lost. Emitting it as a main statement is semantically
        # wrong and may generate constraint-violation reports.

        # P1412 = languages spoken, written or signed.
        # Bug fix 2026-04-15 (web audit): previously hardcoded to Hebrew.
        # Bug fix 2026-04-16 (deeper audit Fix #12): the manuscript's MARC
        # 008/041 languages are MANUSCRIPT-level data, NOT person-level.
        # A scribe who only copied a Hebrew manuscript may not have written
        # Hebrew themselves; an owner mentioned in provenance may speak
        # something else entirely. Only emit P1412 when the role is
        # "author" — for that role the manuscript's language is a defensible
        # proxy for the author's writing language. For all other roles
        # (scribe, owner, mentioned-person), omit P1412 to avoid asserting
        # something we cannot defend.
        role_norm = role.strip().lower() if role else ""
        if not is_org and role_norm == "author":
            seen_lang_qids: set[str] = set()
            for lang_code in source_record.get("languages") or []:
                lang_qid = LANG_TO_QID.get(str(lang_code))
                if lang_qid and lang_qid not in seen_lang_qids:
                    seen_lang_qids.add(lang_qid)
                    person.statements.append(
                        WikidataStatement(
                            property_id="P1412",
                            value=lang_qid,
                            value_type="item",
                        )
                    )

        # P1559 = name in native language — use natural order, matching the public label.
        # Catalog inverted forms are source evidence, not the Wikidata value.
        cleaned_name = name.strip().rstrip(",;:")
        if pref_heb and not is_org:
            inverted_heb = _normalise_label(
                _to_natural_name_order(_strip_person_name_qualifiers(pref_heb))
            )
            person.statements = [
                s for s in person.statements if getattr(s, "property_id", "") != "P1559"
            ]
            person.statements.append(
                WikidataStatement(
                    property_id="P1559",
                    value=inverted_heb,
                    value_type="monolingualtext",
                    language="he",
                )
            )
        elif cleaned_name and not is_org and len(cleaned_name) >= 2:
            # Detect script: Hebrew, Cyrillic, Arabic. Latin is intentionally
            # excluded because we cannot reliably infer the true language.
            if any("\u0590" <= c <= "\u05ff" for c in cleaned_name):
                native_lang = "he"
            elif any("\u0400" <= c <= "\u04ff" for c in cleaned_name):
                native_lang = "ru"
            elif any("\u0600" <= c <= "\u06ff" for c in cleaned_name):
                native_lang = "ar"
            else:
                native_lang = None  # Latin and unknown scripts → omit P1559

            if native_lang:
                person.statements.append(
                    WikidataStatement(
                        property_id="P1559",
                        value=_normalise_label(_to_natural_name_order(cleaned_name)),
                        value_type="monolingualtext",
                        language=native_lang,
                    )
                )

        # Additional authority IDs from VIAF cluster harvesting
        for match in source_record.get("marc_authority_matches") or []:
            mid = str(match.get("mazal_id", ""))
            vid = str(match.get("viaf_uri", ""))
            if (mazal_id and mid == mazal_id) or (viaf_uri and vid == viaf_uri):
                if match.get("gnd_id"):
                    person.statements.append(
                        WikidataStatement(
                            property_id="P227",
                            value=str(match["gnd_id"]),
                            value_type="external-id",
                        )
                    )
                if match.get("lc_id"):
                    person.statements.append(
                        WikidataStatement(
                            property_id="P244",
                            value=str(match["lc_id"]),
                            value_type="external-id",
                        )
                    )
                if match.get("isni"):
                    person.statements.append(
                        WikidataStatement(
                            property_id="P213",
                            value=str(match["isni"]),
                            value_type="external-id",
                        )
                    )
                if match.get("bnf_id"):
                    person.statements.append(
                        WikidataStatement(
                            property_id="P268",
                            value=str(match["bnf_id"]),
                            value_type="external-id",
                        )
                    )
                break

        # Bug fix 2026-04-16 (deeper audit Fix #1): attach person_ref to
        # every statement that does not already carry references. Done as
        # a post-build pass so each `WikidataStatement(...)` callsite above
        # does not need an explicit references= kwarg.
        if person_ref:
            for stmt in person.statements:
                if not stmt.references:
                    stmt.references = list(person_ref)

        self._person_items[key] = person
        return person
