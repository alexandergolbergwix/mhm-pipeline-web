"""Work item construction for Wikidata projections."""

from __future__ import annotations

from converter.wikidata.item_builder import (
    LANG_TO_QID,
    P_AUTHOR,
    P_INSTANCE_OF,
    P_LANGUAGE,
    P_TITLE,
    Q_WRITTEN_WORK,
    WikidataItem,
    WikidataStatement,
    _associate_item_with_source_record,
    _build_work_description,
    _extract_century_for_work,
    _has_hebrew_script,
    _normalise_label,
    _person_key,
    _strip_name_quotes,
    _work_key,
    english_label_for_hebrew,
    logger,
    nli_reference,
)


class WorkProjectionMixin:
    def _get_or_create_work(
        self,
        title: str,
        author_name: str | None,
        source_record: dict[str, object],
    ) -> WikidataItem:
        """Get existing or create new work item for a Hebrew manuscript work."""
        # Strip trailing MARC ISBD punctuation (", ;", " :", " .", " /") BEFORE
        # the title is used for deduplication keys, labels, and reconciler
        # queries. Rule 33 handled P1476 and manuscript labels; this extends
        # the same hygiene to work items. Discovered 2026-04-24 via validator
        # error on 'פרושי תפלה מלוקטים (קטעים) :'.
        # Also strip surrounding MARC-style quotation marks (ASCII " and all
        # Unicode typographic quote chars in _QUOTE_CHARS) so titles stored as
        # '"Diodati Segre"' or '"""כתובים"""' don't land in labels/aliases/
        # P1476 with spurious surrounding quotes.
        title = _strip_name_quotes(title).rstrip(" .,;:/-")
        key = _work_key(title)
        if key in self._work_items:
            work = self._work_items[key]
            _associate_item_with_source_record(work, source_record)
            return work

        # Bug fix 2026-04-15 (web audit Fix #2): consult the reconciler to
        # find an existing Wikidata work matching this Hebrew title before
        # creating a duplicate. The KNOWN_WORK_QIDS hardcoded mapping is a
        # fast first pass (handled by callers); this is the SPARQL fallback
        # for works not in that list. The reconciler's
        # reconcile_work_by_label_and_author() also rejects matches whose
        # P50 author conflicts with our proposed author (cross-verification
        # pattern reused from person reconciliation).
        existing_qid: str | None = None
        if self._reconciler is not None:
            try:
                # Resolve author QID from cache when available so the
                # reconciler can perform author-conflict rejection.
                author_qid: str | None = None
                if author_name and author_name.strip():
                    author_key = _person_key(author_name, None, None)
                    author_qid = self._person_qids.get(author_key)
                existing_qid = self._reconciler.reconcile_work_by_label_and_author(
                    title=title,
                    lang="he",
                    author_qid=author_qid,
                )
            except Exception as exc:  # noqa: BLE001 - reconciler failures must not block builds
                logger.warning(
                    "reconcile_work_by_label_and_author failed for %r: %s; "
                    "proceeding with new-item creation",
                    title,
                    exc,
                )

        work = WikidataItem(entity_type="work", local_id=key)
        _associate_item_with_source_record(work, source_record)
        if existing_qid:
            work.existing_qid = existing_qid
        # Latin-only work titles (e.g. "Bible", "Diodati Segre") must NOT
        # land in the he-label slot — validator rule HE_LABEL_IS_LATIN.
        # Route them to en only and store as a he-alias for searchability.
        title = _normalise_label(title)
        if _has_hebrew_script(title):
            work.labels["he"] = title
            if title and all(ord(c) < 256 for c in title if c.isalpha()):
                work.labels["en"] = title
            else:
                # Rule 46 (2026-05-18, fourth iteration): TaatikNet (the
                # `malper/taatiknet` ByT5 transliterator that replaced the
                # broken DICTA Nakdan) now powers Tier 4 and produces
                # real readable output, e.g. "Takanut rivno gereshem meor
                # hagola" for the user's canonical example. Tier 5
                # (consonantal ALA-LC) stays disabled — its "Tknot rvno..."
                # output is still too ugly.
                #
                # Use the transliteration only; catalog IDs stay in P3959
                # and source metadata, never in public labels.
                trusted_romanization = any(
                    str(source_record.get(key) or "").strip()
                    for key in (
                        "title_romanized", "title_alalc", "title_latin",
                        "title_en", "marc_880", "marc_246",
                        "variant_title_romanized", "alt_title_romanized",
                    )
                )
                if trusted_romanization:
                    en_candidate = english_label_for_hebrew(
                        title,
                        source_record,
                        allow_nakdan=False,
                        allow_algorithmic=False,
                    )
                    if en_candidate:
                        work.labels["en"] = en_candidate
        else:
            # Latin-only title (e.g. "Bible", "Diodati Segre"): route to the en
            # slot only — never any he slot (label OR alias). Putting Latin text
            # in any he-tagged slot was the original Kolja21/Geagea complaint.
            work.labels["en"] = title
            if title not in (work.aliases.get("en") or []):
                work.aliases.setdefault("en", []).append(title)
        # Bug fix 2026-04-15 (web audit): all 3,970 work items previously
        # received the identical description "Hebrew manuscript work", which
        # made same-label items indistinguishable on Wikidata. Build a
        # disambiguating description that includes the author when known
        # (Wikidata requires descriptions to disambiguate same-label items).
        work.descriptions["en"] = _build_work_description(
            author_name=author_name,
            century=_extract_century_for_work(source_record),
        )

        work.statements.append(
            WikidataStatement(
                property_id=P_INSTANCE_OF,
                value=Q_WRITTEN_WORK,
                value_type="item",
            )
        )
        title_language = "he" if _has_hebrew_script(title) else "en"
        work.statements.append(
            WikidataStatement(
                property_id=P_TITLE,
                value=title,
                value_type="monolingualtext",
                language=title_language,
            )
        )
        # A manuscript language is not automatically the language of every
        # contained work. Emit P407 only when work-specific language evidence
        # is supplied by an authority/curator channel.
        work_language_values = source_record.get("work_languages") or []
        if isinstance(work_language_values, str):
            work_language_values = [work_language_values]
        for language_code in work_language_values:
            language_qid = LANG_TO_QID.get(str(language_code))
            if language_qid:
                work.statements.append(
                    WikidataStatement(
                        property_id=P_LANGUAGE,
                        value=language_qid,
                        value_type="item",
                    )
                )

        # Link to author if available
        if author_name and author_name.strip():
            author_key = _person_key(author_name, None, None)
            person = self._person_items.get(author_key)
            if person:
                resolved_qid = self._person_qids.get(author_key) or person.existing_qid
                if resolved_qid:
                    work.statements.append(
                        WikidataStatement(
                            property_id=P_AUTHOR,
                            value=resolved_qid,
                            value_type="item",
                        )
                    )
                else:
                    work.statements.append(
                        WikidataStatement(
                            property_id=P_AUTHOR,
                            value=f"__LOCAL:{person.local_id}",
                            value_type="item",
                        )
                    )

        # Bug fix 2026-04-16 (deeper audit Fix #2): attach a P248 reference
        # to every work statement. Use the parent manuscript's NLI catalog
        # URL — the work was extracted from that record.
        ms_ctrl = str(source_record.get("_control_number") or "")
        if ms_ctrl:
            work_ref = nli_reference(ms_ctrl)
            for stmt in work.statements:
                if not stmt.references:
                    stmt.references = list(work_ref)

        self._work_items[key] = work
        return work
