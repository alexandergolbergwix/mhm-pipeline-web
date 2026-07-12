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


class ContentProjectionMixin:
    def _add_contents(
        self,
        item: WikidataItem,
        record: dict[str, object],
        ref: list[dict[str, str]],
    ) -> None:
        """Add P1574 (exemplar of) for contained works.

        Links manuscript to known Wikidata work items when possible.
        Also processes NER-extracted WORK entities from MARC 505.
        """
        seen_works: set[str] = set()
        approved_work_titles = {
            _clean_work_title(str(match.get("name") or match.get("matched_name") or "")).casefold()
            for match in (record.get("marc_authority_matches") or [])
            if isinstance(match, dict)
            and str(match.get("role") or "").strip().lower()
            in {"contained work", "work", "exemplar of", "related work"}
        }

        # From structured MARC 505 data
        for content in record.get("contents") or []:
            work_title = (
                str(content.get("title", "")) if isinstance(content, dict) else str(content)
            )
            if not work_title or not work_title.strip():
                continue
            work_title = work_title.strip()
            if work_title in seen_works:
                continue
            seen_works.add(work_title)

            qualifiers: list[dict[str, object]] = []
            if isinstance(content, dict) and content.get("folio_range"):
                qualifiers.append(
                    {
                        "property": "P958",
                        "value": str(content["folio_range"]),
                        "type": "string",
                    }
                )

            work_qid = KNOWN_WORK_QIDS.get(work_title)
            if (
                not work_qid
                and _clean_work_title(work_title).casefold() not in approved_work_titles
            ):
                continue
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
            else:
                # Work identified by title matching only (not in KNOWN_WORK_QIDS
                # and not reconciled to an existing Wikidata item). Add P1480
                # (presumably) to express `possibly_realises` — the manuscript
                # may exemplify this work, but the identification is uncertain.
                work_item = self._get_or_create_work(work_title, None, record)
                item.statements.append(
                    WikidataStatement(
                        property_id=P_EXEMPLAR_OF,
                        value=f"__LOCAL:{work_item.local_id}",
                        value_type="item",
                        qualifiers=qualifiers
                        + [
                            {
                                "property": P_SOURCING_CIRCUMSTANCES,
                                "value": Q_PRESUMABLY,
                                "type": "item",
                            }
                        ],
                        references=ref,
                    )
                )

        # From NER-extracted contents entities (WORK + FOLIO)
        entities = record.get("entities") or []
        cont_entities = [e for e in entities if e.get("source") == "contents_ner"]
        works = [e for e in cont_entities if e.get("type") == "WORK"]
        folios = [e for e in cont_entities if e.get("type") == "FOLIO"]

        for work in works:
            if work.get("approved") is False:
                continue
            work_title_raw = str(work.get("text", "")).strip().strip(_QUOTE_CHARS + ".")
            work_title, embedded_author = _split_work_title_author(work_title_raw)
            if not work_title or work_title in seen_works:
                continue
            work_title = _clean_work_title(work_title)
            if _is_noise_work_title(work_title):
                continue
            seen_works.add(work_title)

            work_qid = KNOWN_WORK_QIDS.get(work_title)
            qualifiers_ner: list[dict[str, object]] = []

            # Find associated folio entity (nearest by position)
            for folio in folios:
                if abs(folio.get("end", 0) - work.get("start", 0)) < 3:
                    qualifiers_ner.append(
                        {
                            "property": "P958",
                            "value": str(folio.get("text", "")).strip(":"),
                            "type": "string",
                        }
                    )
                    break

            if work_qid:
                item.statements.append(
                    WikidataStatement(
                        property_id=P_EXEMPLAR_OF,
                        value=work_qid,
                        value_type="item",
                        qualifiers=qualifiers_ner,
                        references=ref,
                    )
                )
            else:
                # Use embedded author extracted from the WORK title text as fallback
                author_name = embedded_author
                # Then try the WORK_AUTHOR positional lookup (overrides embedded_author if found)
                work_authors = [e for e in cont_entities if e.get("type") == "WORK_AUTHOR"]
                for wa in work_authors:
                    if abs(wa.get("start", 0) - work.get("end", 0)) < 20:
                        author_name = str(wa.get("text", "")).strip()
                        break
                work_item = self._get_or_create_work(work_title, author_name, record)
                # NER-identified works are inherently uncertain → add P1480 (presumably)
                item.statements.append(
                    WikidataStatement(
                        property_id=P_EXEMPLAR_OF,
                        value=f"__LOCAL:{work_item.local_id}",
                        value_type="item",
                        qualifiers=qualifiers_ner
                        + [
                            {
                                "property": P_SOURCING_CIRCUMSTANCES,
                                "value": Q_PRESUMABLY,
                                "type": "item",
                            }
                        ],
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
