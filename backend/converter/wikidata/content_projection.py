"""Contents, genre, and subject projection for Wikidata items."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

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
from converter.wikidata.property_mapping import extend_source_reference
from converter.wikidata.work_candidates import assess_work_candidate
from converter.wikidata.work_link_specificity import (
    refine_exemplar_work_qid,
    should_emit_unknown_text_exemplar,
)

_QID_RE = re.compile(r"^Q\d+$")
_BROAD_MAIN_SUBJECT_TERMS = {"jews"}
_VERIFIED_PRIMARY_SUBJECT_TERMS = {"masorah"}


_PRIMARY_SUBJECT_KEYS = ("primary", "is_primary", "main_subject", "is_main_subject")


@dataclass(frozen=True, slots=True)
class StructuredWorkAttribution:
    """One title and its explicit author evidence from a MARC content entry."""

    title: str
    author_name: str | None
    author_source: str | None


_TITLE_RESPONSIBILITY_PATTERNS = (
    re.compile(r"^(?P<title>.+?)\s+/\s*(?P<author>.+?)\s*$"),
    re.compile(
        r"^(?P<title>.+?)\s+[\-–—]\s+(?:by|מאת)\s+(?P<author>.+?)\s*$",
        re.IGNORECASE,
    ),
)
_RESPONSIBILITY_PREFIX_RE = re.compile(r"^(?:by|מאת)\s+", re.IGNORECASE)
_EXPLICIT_COMPOSITION_MARKER_RE = re.compile(
    r"\s+(?:חברו|חיברו|חיברוהו|מחברו)\s+",
    re.IGNORECASE,
)


def _structured_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("name", "heading", "text", "label", "value"):
            text = _structured_text(value.get(key))
            if text:
                return text
    if isinstance(value, (list, tuple)):
        for member in value:
            text = _structured_text(member)
            if text:
                return text
    return ""


def source_work_title(entry: dict[str, object]) -> str:
    source_entry = {**entry, "title": entry.get("source_title") or entry.get("title")}
    title = structured_work_attribution(source_entry).title.replace('""', '"').strip()
    if len(title) > 1 and title[0] == title[-1] and title[0] in {'"', "'", "״"}:
        title = title[1:-1].strip()
    return title


def structured_work_attribution(
    entry: dict[str, Any],
) -> StructuredWorkAttribution:
    """Extract one title and author from a structured MARC content entry."""
    raw_title = _structured_text(entry.get("title"))
    title = raw_title
    embedded_author: str | None = None
    author_source: str | None = None
    for pattern in _TITLE_RESPONSIBILITY_PATTERNS:
        match = pattern.fullmatch(raw_title)
        if match:
            title = match.group("title").strip()
            embedded_author = _RESPONSIBILITY_PREFIX_RE.sub(
                "", match.group("author").strip(),
            )
            author_source = "title_responsibility_separator"
            break
    if embedded_author is None and _EXPLICIT_COMPOSITION_MARKER_RE.search(raw_title):
        title, embedded_author = _split_work_title_author(raw_title)
        author_source = "title" if embedded_author else None
    for key in (
        "author", "author_name", "work_author", "responsibility",
        "statement_of_responsibility",
    ):
        author_name = _structured_text(entry.get(key))
        if author_name:
            return StructuredWorkAttribution(title, author_name, key)
    return StructuredWorkAttribution(
        title,
        embedded_author,
        author_source,
    )


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
    """Read an author from a structured MARC content entry."""
    return structured_work_attribution(entry).author_name


def _work_genre_qid(entry: dict[str, object]) -> str | None:
    genre = entry.get("genre")
    if str(entry.get("source_field") or "") in {"500", "505"} and re.match(
        r"^(?:העתק מן הכתב ששלח|אגרת מאת|איגרת מאת|מכתב מאת)\s", str(entry.get("title") or ""),
    ):
        genre = "Letters"
    if not isinstance(genre, str) or not genre.strip():
        return None
    from converter.wikidata.marc_subject_resolve import resolve_genre_qid  # noqa: PLC0415

    return resolve_genre_qid(genre, allow_network=False)


def letter_content_occurrences(record: dict[str, object]) -> list[dict[str, object]]:
    return [entry for entry in record.get("contents") or []
            if isinstance(entry, dict) and _work_genre_qid(entry) == "Q133492"]


def _content_genre_qids(record: dict[str, object]) -> set[str]:
    return {
        qid for entry in record.get("contents") or []
        if isinstance(entry, dict) and (qid := _work_genre_qid(entry))
    }


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


def _content_author(
    record: dict[str, object],
    attribution: StructuredWorkAttribution,
    entry: dict[str, object],
) -> str | None:
    """Use a record author only when it identifies one unambiguous work."""
    if attribution.author_name:
        return attribution.author_name
    content_entries = [
        content if isinstance(content, dict) else {"title": content}
        for content in record.get("contents") or []
        if _structured_text(
            content.get("title") if isinstance(content, dict) else content
        )
    ]
    source_field = str(entry.get("source_field") or "505").strip().upper()
    if (len(content_entries) == 1 and source_field == "505") or (
        _work_title_key(attribution.title)
        and _work_title_key(attribution.title) == _work_title_key(record.get("title"))
    ):
        return _record_author(record)
    return None


def _record_projects_main_work(record: dict[str, object]) -> bool:
    """True when the 100/245 main-work gate in ``_add_contents`` passes.

    Mirror of that gate without its ``seen_works`` bookkeeping. A
    secondary content work must not inherit the record author (W-296:
    the Etz Chayim selection must not inherit Bagi), so the NER author
    fallback below applies only when the record projects no main work
    and the NER work is its sole content work.
    """
    title = _clean_work_title(str(record.get("title") or ""))
    author_name = _record_author(record) or ""
    if not title or not author_name or _is_noise_work_title(title, allow_latin=True):
        return False
    decision = assess_work_candidate(
        title,
        source_field="100/245",
        approved=True,
        candidate_kind="marc_title_author",
        source_text=f"{title} — {author_name}",
    )
    return bool(decision.accepted and decision.title)


def _entry_work_qid(entry: dict[str, object]) -> str | None:
    for key in ("wikidata_id", "wikidata_qid", "existing_qid"):
        qid = _safe_work_qid(entry.get(key))
        if qid:
            return qid
    return None


def _source_work_authors(record: dict[str, object], title: str) -> set[str]:
    names = {
        _work_title_key(attribution.author_name or "")
        for content in record.get("contents") or []
        if isinstance(content, dict)
        and _work_title_key((attribution := structured_work_attribution(content)).title)
        == _work_title_key(title)
        and attribution.author_name
    }
    if _work_title_key(record.get("title")) == _work_title_key(title) and (
        author := _record_author(record)
    ):
        names.add(_work_title_key(author))
    return names


def _work_match_author_compatible(
    record: dict[str, object], title: str, author_name: str | None, match: dict[str, object],
) -> bool:
    payload = match.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    matched_author = _structured_text(
        match.get("author_name")
        or match.get("author")
        or payload.get("author_name")
        or payload.get("author")
    )
    if matched_author:
        return bool(author_name and _work_title_key(matched_author) == _work_title_key(author_name))
    return len(_source_work_authors(record, title)) <= 1


def _approved_work_matches(
    record: dict[str, object], title: str, author_name: str | None
) -> list[dict[str, object]]:
    return [
        match
        for match in record.get("marc_authority_matches") or []
        if isinstance(match, dict)
        and match.get("approved") is not False
        and str(match.get("role") or "").strip().casefold().replace("_", " ")
        in {"contained work", "work", "exemplar of", "related work"}
        and _work_title_key(match.get("name") or match.get("matched_name"))
        == _work_title_key(title)
        and _work_match_author_compatible(record, title, author_name, match)
    ]


def _work_qid_for_record_title(
    record: dict[str, object],
    title: str,
    author_name: str | None = None,
) -> str | None:
    """Find an exact approved or structured work QID for a record title."""
    target = _work_title_key(title)
    candidates: set[str] = set()
    for content in record.get("contents") or []:
        entry = content if isinstance(content, dict) else {"title": str(content)}
        attribution = structured_work_attribution(entry)
        if _work_title_key(attribution.title) == target:
            if not _work_match_author_compatible(
                record, title, author_name, {"author_name": attribution.author_name}
            ):
                continue
            qid = _entry_work_qid(entry)
            if qid:
                candidates.add(qid)
    for match in _approved_work_matches(record, title, author_name):
        payload = match.get("payload")
        qid = _safe_work_qid(
            match.get("wikidata_qid")
            or (payload.get("wikidata_qid") if isinstance(payload, dict) else ""),
        )
        if qid:
            candidates.add(qid)
    if candidates:
        return next(iter(candidates)) if len(candidates) == 1 else None
    return (
        known_work_qid_for_title(title) if len(_source_work_authors(record, title)) <= 1 else None
    )


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
        author_name: str | None = None,
    ) -> str | None:
        return _work_qid_for_record_title(record, title, author_name)

    def _work_author_for_record_title(
        self,
        record: dict[str, object],
        title: str,
    ) -> str | None:
        """Find an author attached to the exact work title in the record."""
        target = _work_title_key(title)
        authors: set[str] = set()
        for content in record.get("contents") or []:
            entry = content if isinstance(content, dict) else {"title": str(content)}
            attribution = structured_work_attribution(entry)
            if _work_title_key(attribution.title) == target:
                author = _content_author(record, attribution, entry)
                if author:
                    authors.add(author)
        if authors:
            return next(iter(authors)) if len(authors) == 1 else None

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
        seen_occurrences: set[tuple[str, str, str]] = set()

        def remember_evidence(target: WikidataItem, evidence: dict[str, object]) -> None:
            if evidence not in target.work_candidate_evidence:
                target.work_candidate_evidence.append(evidence)

        for content in record.get("contents") or []:
            entry = content if isinstance(content, dict) else {"title": str(content)}
            raw_title = _structured_text(entry.get("title"))
            attribution = structured_work_attribution(entry)
            candidate_title = attribution.title
            embedded_author = _content_author(record, attribution, entry)
            cleaned = _clean_work_title(candidate_title)
            work_qid = (
                _entry_work_qid(entry)
                or self._work_qid_for_record_title(record, cleaned or raw_title, embedded_author)
            )
            work_qid = refine_exemplar_work_qid(
                work_qid, title=cleaned or raw_title, record=record,
            )
            approved = bool(_approved_work_matches(record, cleaned or raw_title, embedded_author))
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
            genre_qid = _work_genre_qid(entry)
            if genre_qid:
                evidence.update({"genre_qid": genre_qid, "scope": "contained_work"})
            if entry.get("identity_scope") == "record":
                evidence["identity_scope"] = "record"
            if attribution.author_name:
                evidence["author_name"] = attribution.author_name
                evidence["author_source"] = attribution.author_source
            remember_evidence(item, evidence)
            work_title = decision.title
            key = work_title.casefold()
            occurrence = (
                key, _work_title_key(embedded_author or ""), str(decision.folio_range or ""),
            )
            if not decision.accepted or not work_title or occurrence in seen_occurrences:
                continue
            seen_occurrences.add(occurrence)
            seen_works.add(key)

            named_as = source_work_title(entry) or work_title or cleaned or raw_title
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
                work_item = self._get_or_create_work(
                    work_title, embedded_author, record,
                    source_scoped=entry.get("identity_scope") == "record",
                )
                remember_evidence(work_item, evidence)
                if genre_qid and not any(
                    s.property_id == P_GENRE and s.value == genre_qid
                    for s in work_item.statements
                ):
                    work_item.statements.append(WikidataStatement(
                        property_id=P_GENRE, value=genre_qid, value_type="item", references=ref,
                    ))
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
                else:
                    evidence.update({
                        "projection_status": "deferred",
                        "projection_reason": "unknown_text_blocked_by_existing_exemplar",
                    })

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
            folio_range = ""
            for folio in folios:
                if abs(int(folio.get("end") or 0) - int(work.get("start") or 0)) < 3:
                    folio_range = str(folio.get("text") or "").strip(":")
                    break
            author_name = embedded_author
            for work_author in cont_entities:
                if work_author.get("type") == "WORK_AUTHOR" and abs(
                    int(work_author.get("start") or 0) - int(work.get("end") or 0)
                ) < 20:
                    author_name = str(work_author.get("text") or "").strip()
                    break
            work_qid = refine_exemplar_work_qid(
                self._work_qid_for_record_title(record, work_title, author_name),
                title=work_title or raw, record=record,
            )
            decision = assess_work_candidate(
                work_title,
                source_field="contents_ner",
                approved=work.get("approved") is not False,
                known_qid=work_qid,
                candidate_kind="named_work",
                folio_range=folio_range,
                source_text=raw,
            )
            evidence = decision.evidence()
            evidence.update({"source_start": work.get("start"), "source_end": work.get("end")})
            if author_name:
                evidence["author_name"] = author_name
            elif len(_source_work_authors(record, work_title)) > 1:
                evidence.update({"accepted": False, "reason": "ambiguous_source_work_author"})
                remember_evidence(item, evidence)
                continue
            remember_evidence(item, evidence)
            key = decision.title.casefold()
            if (
                not decision.accepted
                or not decision.title
                or _is_noise_work_title(decision.title)
            ):
                continue
            if not author_name and (
                _work_title_key(decision.title) == _work_title_key(record.get("title"))
                or (
                    len(works) == 1
                    and not seen_works - {key}
                    and not _record_projects_main_work(record)
                )
            ):
                author_name = _record_author(record)
            work_qid = refine_exemplar_work_qid(
                self._work_qid_for_record_title(record, decision.title, author_name),
                title=decision.title, record=record,
            )
            occurrence = (key, _work_title_key(author_name or ""), folio_range)
            if occurrence in seen_occurrences:
                continue
            seen_occurrences.add(occurrence)
            seen_works.add(key)
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
        # a work candidate even when the catalogue also extracted secondary
        # 500 work mentions (e.g. a treatise followed by supplementary notes).
        title = _clean_work_title(str(record.get("title") or ""))
        author_name = _record_author(record) or ""
        main_identity = (title.casefold(), _work_title_key(author_name))
        if (
            title
            and author_name
            and not any(occurrence[:2] == main_identity for occurrence in seen_occurrences)
        ):
            decision = assess_work_candidate(
                title,
                source_field="100/245",
                approved=True,
                candidate_kind="marc_title_author",
                source_text=f"{title} — {author_name}",
            )
            evidence = decision.evidence()
            if _is_noise_work_title(title, allow_latin=True):
                evidence.update({"accepted": False, "reason": "catalogue_noise_title"})
            remember_evidence(item, evidence)
            if evidence["accepted"] and decision.title:
                seen_works.add(decision.title.casefold())
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
                            catalog_title=str(record.get("title") or decision.title).replace(
                                '""', '"'
                            ),
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
        scoped_genres = _content_genre_qids(record)

        def _append_genre(
            qid: str, *, qualifiers: list | None = None, references: list | None = None
        ) -> None:
            if not qid or qid in seen_genre_qids or qid in scoped_genres:
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
                heuristic_ref = extend_source_reference(ref, [
                    {"property": P_BASED_ON_HEURISTIC, "value": "Q2539", "type": "item"},
                ])
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
        scoped_genres = _content_genre_qids(record)

        def _append_p921(qid: str) -> None:
            if not qid or qid in seen_qids or qid in scoped_genres:
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
            if stype == "topic":
                # The gate is a *verified QID*, not an explicit "primary"
                # marker (Rule W-281). `resolve_subject_qid` is fail-closed:
                # it answers only from the static crosswalk or a
                # pre-stamped authority `wikidata_id`, never from a guess.
                # A hit is therefore the MARC/authority evidence Rule R26
                # requires for the exact Wikidata meaning. The earlier
                # `_is_primary_subject` requirement dropped 463 resolvable
                # term-occurrences in the 17th-century corpus and held P921
                # at 2.3%.
                qid = resolve_subject_qid(subj, allow_network=False)
                if qid:
                    _append_p921(qid)
                continue
            if (
                not _is_primary_subject(subj)
                and term.casefold() not in _VERIFIED_PRIMARY_SUBJECT_TERMS
            ):
                # Non-topic 6XX (person/place/corporate) still needs an
                # explicit primary marker before it becomes a main subject.
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
