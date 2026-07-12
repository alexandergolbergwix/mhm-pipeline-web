"""Contents, genre, and subject projection for Wikidata items."""

from __future__ import annotations

from converter.wikidata.item_builder import (
    KNOWN_WORK_QIDS,
    P_BASED_ON_HEURISTIC,
    P_EXEMPLAR_OF,
    P_GENRE,
    P_MAIN_SUBJECT,
    P_NATURE_OF_STATEMENT,
    P_SOURCING_CIRCUMSTANCES,
    Q_HYPOTHESIS,
    Q_PRESUMABLY,
    WikidataItem,
    WikidataStatement,
    _QUOTE_CHARS,
    _clean_work_title,
    _get_genre_classifier,
    _is_noise_work_title,
    _person_key,
    _split_work_title_author,
)
from converter.wikidata.work_candidates import assess_work_candidate


class ContentProjectionMixin:
    def _add_contents(
        self,
        item: WikidataItem,
        record: dict[str, object],
        ref: list[dict[str, str]],
    ) -> None:
        """Add P1574 only for source-backed, identifiable contained works."""
        seen_works: set[str] = set()
        approved_work_titles = {
            _clean_work_title(str(match.get("name") or match.get("matched_name") or "")).casefold()
            for match in (record.get("marc_authority_matches") or [])
            if isinstance(match, dict)
            and bool(match.get("approved", True))
            and str(match.get("role") or "").strip().lower()
            in {"contained work", "work", "exemplar of", "related work"}
        }

        def remember_evidence(target: WikidataItem, evidence: dict[str, object]) -> None:
            if evidence not in target.work_candidate_evidence:
                target.work_candidate_evidence.append(evidence)

        for content in record.get("contents") or []:
            entry = content if isinstance(content, dict) else {"title": str(content)}
            raw_title = str(entry.get("title") or "").strip()
            candidate_title, embedded_author = _split_work_title_author(raw_title)
            cleaned = _clean_work_title(candidate_title)
            work_qid = KNOWN_WORK_QIDS.get(cleaned) or KNOWN_WORK_QIDS.get(raw_title)
            approved = cleaned.casefold() in approved_work_titles
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

            qualifiers: list[dict[str, object]] = []
            if decision.folio_range:
                qualifiers.append(
                    {"property": "P958", "value": decision.folio_range, "type": "string"}
                )

            if work_qid:
                item.statements.append(
                    WikidataStatement(
                        property_id=P_EXEMPLAR_OF,
                        value=work_qid,
                        value_type="item",
                        qualifiers=qualifiers,
                        references=ref,
                    )
                )
                continue

            work_item = self._get_or_create_work(work_title, embedded_author, record)
            remember_evidence(work_item, evidence)
            item.statements.append(
                WikidataStatement(
                    property_id=P_EXEMPLAR_OF,
                    value=f"__LOCAL:{work_item.local_id}",
                    value_type="item",
                    qualifiers=qualifiers
                    + [{
                        "property": P_SOURCING_CIRCUMSTANCES,
                        "value": Q_PRESUMABLY,
                        "type": "item",
                    }],
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
            work_qid = KNOWN_WORK_QIDS.get(work_title)
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

            qualifiers: list[dict[str, object]] = []
            for folio in folios:
                if abs(int(folio.get("end") or 0) - int(work.get("start") or 0)) < 3:
                    qualifiers.append({
                        "property": "P958",
                        "value": str(folio.get("text") or "").strip(":"),
                        "type": "string",
                    })
                    break

            if work_qid:
                item.statements.append(
                    WikidataStatement(
                        property_id=P_EXEMPLAR_OF,
                        value=work_qid,
                        value_type="item",
                        qualifiers=qualifiers,
                        references=ref,
                    )
                )
                continue

            author_name = embedded_author
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
                    qualifiers=qualifiers
                    + [{
                        "property": P_SOURCING_CIRCUMSTANCES,
                        "value": Q_PRESUMABLY,
                        "type": "item",
                    }],
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
        from converter.wikidata.marc_subject_resolve import resolve_genre_qid  # noqa: PLC0415

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
                from converter.transformer.subject_records import normalize_genre_entry  # noqa: PLC0415

                norm = normalize_genre_entry(genre)
                label = norm["term"] if norm else ""
            else:
                label = str(genre).strip()
            if not label:
                continue
            qid = resolve_genre_qid(label, genre_entry=entry_by_term.get(label))
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
        from converter.wikidata.marc_subject_resolve import resolve_subject_qid  # noqa: PLC0415
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
            if qid:
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
