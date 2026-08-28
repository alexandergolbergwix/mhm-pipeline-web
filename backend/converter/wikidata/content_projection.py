"""Contents, genre, and subject projection for Wikidata items."""

from __future__ import annotations

import re

from converter.wikidata.item_builder import (
    _QUOTE_CHARS,
    P_BASED_ON_HEURISTIC,
    P_EXEMPLAR_OF,
    P_GENRE,
    P_MAIN_SUBJECT,
    P_NATURE_OF_STATEMENT,
    P_OBJECT_NAMED_AS,
    P_SOURCING_CIRCUMSTANCES,
    Q_HYPOTHESIS,
    Q_PRESUMABLY,
    Q_UNKNOWN_TEXT,
    WikidataItem,
    WikidataStatement,
    _clean_work_title,
    _get_genre_classifier,
    _is_noise_work_title,
    _person_key,
    _split_work_title_author,
    known_work_qid_for_title,
)
from converter.wikidata.work_candidates import assess_work_candidate
from converter.wikidata.work_link_specificity import (
    refine_exemplar_work_qid,
    should_emit_unknown_text_exemplar,
)

_QID_RE = re.compile(r"^Q\d+$")
_BROAD_MAIN_SUBJECT_TERMS = {"jews"}
_VERIFIED_PRIMARY_SUBJECT_TERMS = {"masorah"}


_PRIMARY_SUBJECT_KEYS = ("primary", "is_primary", "main_subject", "is_main_subject")


def _is_primary_subject(subject: dict[str, object]) -> bool:
    """Require an explicit primary marker before promoting a 6XX topic to P921."""
    for key in _PRIMARY_SUBJECT_KEYS:
        value = subject.get(key)
        if value is True or (
            isinstance(value, str)
            and value.strip().casefold() in {"true", "yes", "1"}
        ):
            return True
    source = str(subject.get("source") or "").casefold().replace("_", " ")
    role = str(subject.get("role") or "").casefold().replace("_", " ")
    return source in {"canonical", "canonical reference", "primary subject"} or role in {
        "primary subject", "main subject", "canonical subject",
    }


def _safe_work_qid(value: object) -> str | None:
    candidate = str(value or "").strip()
    return candidate if _QID_RE.fullmatch(candidate) else None


def _work_title_key(value: object) -> str:
    title, _author = _split_work_title_author(_clean_work_title(str(value or "")))
    return title.casefold()


def _entry_author(entry: dict[str, object]) -> str | None:
    """Read an author attached by the contents-NER enrichment pass."""
    for key in ("author", "author_name", "work_author"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _record_author(record: dict[str, object]) -> str | None:
    """Read a primary author before an attributed author (W-206)."""
    allowed_roles = ("author", "presumed author", "attributed author")
    for role_to_use in allowed_roles:
        for author in record.get("authors") or []:
            if not isinstance(author, dict):
                continue
            role = str(author.get("role") or "author").strip().casefold()
            if role != role_to_use:
                continue
            name = str(author.get("name") or "").strip()
            if name:
                return name
        for match in record.get("marc_authority_matches") or []:
            if not isinstance(match, dict) or match.get("approved") is False:
                continue
            role = str(match.get("role") or "").strip().casefold()
            if role != role_to_use:
                continue
            name = str(
                match.get("name")
                or match.get("entity_text")
                or match.get("matched_name")
                or "",
            ).strip()
            if name:
                return name
    return None


def _entry_work_qid(entry: dict[str, object]) -> str | None:
    for key in ("wikidata_id", "wikidata_qid", "existing_qid"):
        qid = _safe_work_qid(entry.get(key))
        if qid:
            return qid
    return None


def _work_qid_for_record_title(
    record: dict[str, object],
    title: str,
) -> str | None:
    """Find an exact approved or structured work QID for a record title."""
    target = _work_title_key(title)
    for content in record.get("contents") or []:
        entry = content if isinstance(content, dict) else {"title": str(content)}
        candidate, _author = _split_work_title_author(str(entry.get("title") or ""))
        if _work_title_key(candidate) == target:
            qid = _entry_work_qid(entry)
            if qid:
                return qid
    for match in record.get("marc_authority_matches") or []:
        if not isinstance(match, dict) or match.get("approved") is False:
            continue
        role = str(match.get("role") or "").strip().casefold().replace("_", " ")
        if role not in {"contained work", "work", "exemplar of", "related work"}:
            continue
        match_title = match.get("name") or match.get("matched_name") or ""
        if _work_title_key(match_title) != target:
            continue
        payload = match.get("payload")
        qid = _safe_work_qid(
            match.get("wikidata_qid")
            or (payload.get("wikidata_qid") if isinstance(payload, dict) else ""),
        )
        if qid:
            return qid
    return known_work_qid_for_title(title)

def _exemplar_qualifiers(
    *,
    catalog_title: str,
    folio_range: str = "",
    presumably: bool = False,
) -> list[dict[str, object]]:
    """Build P1574 qualifiers: catalog form (P1932) + optional folio/section."""
    quals: list[dict[str, object]] = []
    titled = str(catalog_title or "").strip()
    if titled:
        quals.append({
            "property": P_OBJECT_NAMED_AS,
            "value": titled,
            "type": "string",
        })
    folio = str(folio_range or "").strip()
    if folio:
        quals.append({"property": "P958", "value": folio, "type": "string"})
    if presumably:
        quals.append({
            "property": P_SOURCING_CIRCUMSTANCES,
            "value": Q_PRESUMABLY,
            "type": "item",
        })
    return quals


class ContentProjectionMixin:
    def _work_qid_for_record_title(
        self,
        record: dict[str, object],
        title: str,
    ) -> str | None:
        return _work_qid_for_record_title(record, title)

    def _work_author_for_record_title(
        self,
        record: dict[str, object],
        title: str,
    ) -> str | None:
        """Find an author attached to the exact work title in the record."""
        target = _work_title_key(title)
        for content in record.get("contents") or []:
            entry = content if isinstance(content, dict) else {"title": str(content)}
            candidate, embedded_author = _split_work_title_author(
                str(entry.get("title") or ""),
            )
            if _work_title_key(candidate) == target:
                return embedded_author or _entry_author(entry) or _record_author(record)

        entities = [
            entity for entity in record.get("entities") or []
            if isinstance(entity, dict) and entity.get("source") == "contents_ner"
        ]
        for work in (entity for entity in entities if entity.get("type") == "WORK"):
            candidate, embedded_author = _split_work_title_author(
                str(work.get("text") or ""),
            )
            if _work_title_key(candidate) != target:
                continue
            for author in (entity for entity in entities if entity.get("type") == "WORK_AUTHOR"):
                if abs(int(author.get("start") or 0) - int(work.get("end") or 0)) < 20:
                    return str(author.get("text") or "").strip() or embedded_author
            return embedded_author
        return None

    def _add_contents(
        self,
        item: WikidataItem,
        record: dict[str, object],
        ref: list[dict[str, str]],
    ) -> None:
        """Add P1574 only for source-backed, identifiable contained works."""
        seen_works: set[str] = set()
        approved_work_titles: set[str] = set()
        approved_work_qids: dict[str, str] = {}
        for match in record.get("marc_authority_matches") or []:
            if not isinstance(match, dict):
                continue
            if not bool(match.get("approved", True)):
                continue
            match_role = str(match.get("role") or "").strip().lower().replace("_", " ")
            if match_role not in {
                "contained work", "work", "exemplar of", "related work",
            }:
                continue
            match_title = _work_title_key(
                match.get("name") or match.get("matched_name") or ""
            )
            if not match_title:
                continue
            approved_work_titles.add(match_title)
            qid = _safe_work_qid(
                match.get("wikidata_qid")
                or (match.get("payload") or {}).get("wikidata_qid")
            )
            if qid:
                approved_work_qids[match_title] = qid

        def remember_evidence(target: WikidataItem, evidence: dict[str, object]) -> None:
            if evidence not in target.work_candidate_evidence:
                target.work_candidate_evidence.append(evidence)

        for content in record.get("contents") or []:
            entry = content if isinstance(content, dict) else {"title": str(content)}
            raw_title = str(entry.get("title") or "").strip()
            candidate_title, embedded_author = _split_work_title_author(raw_title)
            embedded_author = embedded_author or _entry_author(entry) or _record_author(record)
            cleaned = _clean_work_title(candidate_title)
            work_key = _work_title_key(cleaned)
            work_qid = (
                _entry_work_qid(entry)
                or approved_work_qids.get(work_key)
                or known_work_qid_for_title(cleaned)
                or known_work_qid_for_title(raw_title)
            )
            work_qid = refine_exemplar_work_qid(
                work_qid, title=cleaned or raw_title, record=record,
            )
            approved = work_key in approved_work_titles
            decision = assess_work_candidate(
                candidate_title,
                source_field=entry.get("source_field") or "505",
                approved=approved,
                known_qid=work_qid,
                candidate_kind=entry.get("candidate_kind") or "",
                folio_range=entry.get("folio_range") or "",
                sequence=entry.get("sequence"),
                source_text=entry.get("source_text") or raw_title,
            )
            evidence = decision.evidence()
            remember_evidence(item, evidence)
            work_title = decision.title
            key = work_title.casefold()
            if not decision.accepted or not work_title or key in seen_works:
                continue
            seen_works.add(key)

            named_as = work_title or cleaned or raw_title
            if work_qid:
                local_work = self._find_work_for_title(work_title, embedded_author, record)
                if local_work is not None:
                    self._ensure_work_author(local_work, embedded_author, record)
                item.statements.append(
                    WikidataStatement(
                        property_id=P_EXEMPLAR_OF,
                        value=work_qid,
                        value_type="item",
                        qualifiers=_exemplar_qualifiers(
                            catalog_title=named_as,
                            folio_range=str(decision.folio_range or ""),
                        ),
                        references=ref,
                    )
                )
                continue

            # Prefer a local work item when we have enough identity; otherwise
            # fall back to DS unknown-text (Q234460) + P1932 rather than invent
            # a fuzzy work QID.
            if embedded_author or approved or len(work_title) >= 8:
                work_item = self._get_or_create_work(work_title, embedded_author, record)
                remember_evidence(work_item, evidence)
                item.statements.append(
                    WikidataStatement(
                        property_id=P_EXEMPLAR_OF,
                        value=f"__LOCAL:{work_item.local_id}",
                        value_type="item",
                        qualifiers=_exemplar_qualifiers(
                            catalog_title=named_as,
                            folio_range=str(decision.folio_range or ""),
                            presumably=True,
                        ),
                        references=ref,
                    )
                )
            else:
                existing_exemplars = {
                    str(s.value)
                    for s in item.statements
                    if str(s.property_id) == P_EXEMPLAR_OF
                }
                if should_emit_unknown_text_exemplar(
                    catalog_title=named_as,
                    other_exemplar_qids=existing_exemplars,
                ):
                    item.statements.append(
                        WikidataStatement(
                            property_id=P_EXEMPLAR_OF,
                            value=Q_UNKNOWN_TEXT,
                            value_type="item",
                            qualifiers=_exemplar_qualifiers(
                                catalog_title=named_as,
                                folio_range=str(decision.folio_range or ""),
                                presumably=True,
                            ),
                            references=ref,
                        )
                    )

        entities = record.get("entities") or []
        cont_entities = [
            entity for entity in entities
            if isinstance(entity, dict) and entity.get("source") == "contents_ner"
        ]
        works = [entity for entity in cont_entities if entity.get("type") == "WORK"]
        folios = [entity for entity in cont_entities if entity.get("type") == "FOLIO"]

        for work in works:
            raw = str(work.get("text") or "").strip().strip(_QUOTE_CHARS + ".")
            work_title, embedded_author = _split_work_title_author(raw)
            work_title = _clean_work_title(work_title)
            work_qid = (
                self._work_qid_for_record_title(record, work_title)
                or approved_work_qids.get(_work_title_key(work_title))
            )
            work_qid = refine_exemplar_work_qid(
                work_qid, title=work_title or raw, record=record,
            )
            decision = assess_work_candidate(
                work_title,
                source_field="contents_ner",
                approved=work.get("approved") is not False,
                known_qid=work_qid,
                candidate_kind="named_work",
                source_text=raw,
            )
            evidence = decision.evidence()
            remember_evidence(item, evidence)
            key = decision.title.casefold()
            if (
                not decision.accepted
                or not decision.title
                or _is_noise_work_title(decision.title)
                or key in seen_works
            ):
                continue
            seen_works.add(key)

            folio_range = ""
            for folio in folios:
                if abs(int(folio.get("end") or 0) - int(work.get("start") or 0)) < 3:
                    folio_range = str(folio.get("text") or "").strip(":")
                    break

            author_name = embedded_author or _record_author(record)
            named_as = decision.title or work_title or raw
            if work_qid:
                local_work = self._find_work_for_title(decision.title, author_name, record)
                if local_work is not None:
                    self._ensure_work_author(local_work, author_name, record)
                item.statements.append(
                    WikidataStatement(
                        property_id=P_EXEMPLAR_OF,
                        value=work_qid,
                        value_type="item",
                        qualifiers=_exemplar_qualifiers(
                            catalog_title=named_as,
                            folio_range=folio_range,
                        ),
                        references=ref,
                    )
                )
                continue

            work_authors = [
                entity for entity in cont_entities if entity.get("type") == "WORK_AUTHOR"
            ]
            for work_author in work_authors:
                if abs(
                    int(work_author.get("start") or 0) - int(work.get("end") or 0)
                ) < 20:
                    author_name = str(work_author.get("text") or "").strip()
                    break
            work_item = self._get_or_create_work(decision.title, author_name, record)
            remember_evidence(work_item, evidence)
            item.statements.append(
                WikidataStatement(
                    property_id=P_EXEMPLAR_OF,
                    value=f"__LOCAL:{work_item.local_id}",
                    value_type="item",
                    qualifiers=_exemplar_qualifiers(
                        catalog_title=named_as,
                        folio_range=folio_range,
                        presumably=True,
                    ),
                    references=ref,
                )
            )

        # A MARC 100 author plus a real 245 title is sufficient evidence for
        # a work candidate when the catalogue has no 505 contents list. Keep
        # the author on the work (P50/P2093), and connect the manuscript via
        # P1574 instead of placing P50 directly on the manuscript.
        if not seen_works and not list(record.get("contents") or []):
            title = _clean_work_title(str(record.get("title") or ""))
            author_name = _record_author(record) or ""
            if title and author_name and not _is_noise_work_title(title):
                decision = assess_work_candidate(
                    title,
                    source_field="100/245",
                    approved=True,
                    candidate_kind="marc_title_author",
                    source_text=f"{title} — {author_name}",
                )
                evidence = decision.evidence()
                remember_evidence(item, evidence)
                if decision.accepted and decision.title:
                    work_item = self._get_or_create_work(
                        decision.title, author_name, record
                    )
                    remember_evidence(work_item, evidence)
                    item.statements.append(
                        WikidataStatement(
                            property_id=P_EXEMPLAR_OF,
                            value=f"__LOCAL:{work_item.local_id}",
                            value_type="item",
                            qualifiers=_exemplar_qualifiers(
                                catalog_title=decision.title,
                                presumably=True,
                            ),
                            references=ref,
                        )
                    )

    def _add_marc_genres(
        self,
        item: WikidataItem,
        record: dict[str, object],
        ref: list[dict[str, str]],
        title: str,
    ) -> None:
        """Emit P136 from MARC 655 (WikiProject Manuscripts content properties)."""
        from converter.wikidata.marc_subject_resolve import (
            genre_projection_supported,
            resolve_genre_qid,
        )  # noqa: PLC0415

        marc_genres = list(record.get("genres") or [])
        genre_entries = list(record.get("genre_entries") or [])
        entry_by_term = {
            str(g.get("term") or g.get("name") or ""): g
            for g in genre_entries
            if isinstance(g, dict)
        }
        seen_genre_qids: set[str] = set()

        def _append_genre(
            qid: str, *, qualifiers: list | None = None, references: list | None = None
        ) -> None:
            if not qid or qid in seen_genre_qids:
                return
            seen_genre_qids.add(qid)
            item.statements.append(
                WikidataStatement(
                    property_id=P_GENRE,
                    value=qid,
                    value_type="item",
                    qualifiers=qualifiers or [],
                    references=references or ref,
                )
            )

        for genre in marc_genres:
            if isinstance(genre, dict):
                from converter.transformer.subject_records import (
                    normalize_genre_entry,  # noqa: PLC0415
                )

                norm = normalize_genre_entry(genre)
                label = norm["term"] if norm else ""
            else:
                label = str(genre).strip()
            if not label:
                continue
            genre_entry = entry_by_term.get(label)
            if not genre_projection_supported(label, record, genre_entry):
                continue
            qid = resolve_genre_qid(label, genre_entry=genre_entry)
            if qid:
                _append_genre(qid)

        if not marc_genres and record.get("allow_inferred_genre"):
            _clf = _get_genre_classifier()
            if _clf is not None:
                heuristic_ref = list(ref) + [
                    {"property": P_BASED_ON_HEURISTIC, "value": "Q2539", "type": "item"},
                ]
                inferred = _clf.predict(title, record.get("notes") or [])
                for genre_str, _conf in inferred:
                    if genre_str == "other":
                        continue
                    qid = resolve_genre_qid(genre_str)
                    if qid:
                        _append_genre(
                            qid,
                            qualifiers=[
                                {
                                    "property": P_SOURCING_CIRCUMSTANCES,
                                    "value": Q_PRESUMABLY,
                                    "type": "item",
                                },
                                {
                                    "property": P_NATURE_OF_STATEMENT,
                                    "value": Q_HYPOTHESIS,
                                    "type": "item",
                                },
                            ],
                            references=heuristic_ref,
                        )

        ner_genre_ents = [
            e
            for e in (record.get("entities") or [])
            if e.get("type") == "GENRE" and e.get("approved", False)
        ]
        for ge in ner_genre_ents:
            genre_text = str(ge.get("text", "")).strip()
            if not genre_projection_supported(genre_text, record, ge):
                continue
            qid = resolve_genre_qid(genre_text)
            if qid:
                _append_genre(
                    qid,
                    qualifiers=[
                        {
                            "property": P_SOURCING_CIRCUMSTANCES,
                            "value": Q_PRESUMABLY,
                            "type": "item",
                        },
                    ],
                )


    def _add_canonical_subjects(
        self,
        item: WikidataItem,
        record: dict[str, object],
        ref: list[dict[str, str]],
    ) -> None:
        """Add P921 (main subject) from canonical refs and MARC 650 topics."""
        from converter.transformer.subject_records import subject_term  # noqa: PLC0415
        from converter.wikidata.marc_subject_resolve import (  # noqa: PLC0415
            canonical_reference_grounded_in_subjects,
            resolve_subject_qid,
        )
        from converter.wikidata.property_mapping import (  # noqa: PLC0415
            BIBLE_BOOK_TO_QID,
            TALMUD_TRACTATE_TO_QID,
        )

        seen_qids: set[str] = set()

        def _append_p921(qid: str) -> None:
            if not qid or qid in seen_qids:
                return
            seen_qids.add(qid)
            item.statements.append(
                WikidataStatement(
                    property_id=P_MAIN_SUBJECT,
                    value=qid,
                    value_type="item",
                    references=ref,
                )
            )

        for cr in record.get("canonical_references") or []:
            hierarchy = cr.get("hierarchy", "")
            qid = None
            if hierarchy == "Bible":
                qid = BIBLE_BOOK_TO_QID.get(str(cr.get("book", "")))
            elif hierarchy == "Talmud_Bavli":
                qid = TALMUD_TRACTATE_TO_QID.get(str(cr.get("tractate", "")))
            if qid and canonical_reference_grounded_in_subjects(record, cr):
                _append_p921(qid)

        for subj in record.get("subjects") or []:
            if not isinstance(subj, dict):
                continue
            stype = str(subj.get("type") or "topic").lower()
            if stype in ("place", "organization", "corporate", "meeting"):
                continue
            term = subject_term(subj)
            if not term:
                continue
            if term.casefold() in _BROAD_MAIN_SUBJECT_TERMS:
                continue
            if (
                not _is_primary_subject(subj)
                and term.casefold() not in _VERIFIED_PRIMARY_SUBJECT_TERMS
            ):
                # MARC 650 is a secondary topical index by default. Keep it in
                # source evidence, but do not assert that every topic is the
                # manuscript's Wikidata main subject.
                continue
            if stype == "topic":
                qid = resolve_subject_qid(subj, allow_network=False)
                if qid:
                    _append_p921(qid)
                continue
            if stype == "person":
                person = self._get_or_create_person(
                    term,
                    None,
                    None,
                    "subject",
                    record,
                )
                p_key = _person_key(term, None, None)
                resolved = self._person_qids.get(p_key) or person.existing_qid
                if resolved:
                    _append_p921(resolved)
                elif person.labels and person.local_id:
                    _append_p921(f"__LOCAL:{person.local_id}")
