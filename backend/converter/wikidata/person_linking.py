"""Role-aware person linking for manuscript projections."""

from __future__ import annotations

from converter.wikidata.item_builder import (
    P_AUTHOR,
    P_OBJECT_NAMED_AS,
    P_SOURCING_CIRCUMSTANCES,
    Q_NLI,
    Q_PRESUMABLY,
    ROLE_TO_PID,
    WikidataItem,
    WikidataStatement,
    _is_anonymous_name,
    _is_institutional_name,
    _person_key,
    _strip_name_quotes,
    logger,
)


class PersonLinkingMixin:
    def _add_person_claims(
        self,
        item: WikidataItem,
        record: dict[str, object],
        ref: list[dict[str, str]],
    ) -> None:
        """Add person-related claims using resolved Wikidata QIDs.

        WikiProject Manuscripts / DS data model:
        - P50 (author) belongs on WORK items, never on manuscripts
        - P11603 (transcribed by) goes directly on manuscripts (scribes)
        - P127 (owned by) goes directly on manuscripts (owners)
        - P11105 (annotator), P88 (commissioned by), P110 (illustrator) are
          manuscript-side agent properties

        Authors are linked via P1574: MS → exemplar of → Work → P50 → Author.
        Anonymous / unresolved authorship stays in source evidence only —
        never as manuscript-side P50 (including somevalue).
        """
        seen_person_keys: set[str] = set()

        def _add_person_statement(
            name: str,
            role: str,
            viaf_uri: str | None,
            mazal_id: str | None,
        ) -> None:
            if not name:
                return
            key = _person_key(name, viaf_uri, mazal_id)
            if key in seen_person_keys:
                return

            # Normalize role for lookup (case-insensitive, strip whitespace)
            role_norm = role.strip().lower().replace("_", " ")
            # A seller is not an owner (the data model forbids modelling auction
            # houses as P127) and a censor is not one either. A *former* owner,
            # however, is now a P3342 "significant person" edge rather than a
            # P127 ownership claim, so dropping it only cost us the link: 49
            # approved former-owner rows produced nothing at all (Rule W-146).
            if role_norm in {"seller", "censor"}:
                logger.info("Skipping non-ownership role %r for %r", role, name)
                seen_person_keys.add(key)
                return
            if role_norm in {"editor", "compiler", "contributor"}:
                # Editorial roles are not manuscript-side claims; authorship of
                # contained works belongs on the work item via P1574 → P50.
                logger.info("Skipping work-side editorial role %r for %r", role, name)
                seen_person_keys.add(key)
                return
            pid = ROLE_TO_PID.get(role_norm) or ROLE_TO_PID.get(role.upper())
            if pid is None:
                logger.warning("Skipping unsupported MARC/NER role %r for %r", role, name)
                return
            seen_person_keys.add(key)

            # Institutional "authors" were mis-attached as P50 (Geagea /
            # Q139085958). Re-route institutional names away from authorship.
            if pid == P_AUTHOR and _is_institutional_name(name):
                pid = "P195"  # collection evidence only — no MS claim below
            clean_name = _strip_name_quotes(name)
            # Anonymous authorship must not become manuscript P50 somevalue
            # (Property:P50 forbids any P50 on manuscripts). Keep the signal in
            # MARC/HMO evidence; work projection may still emit P2093 on works.
            if pid == P_AUTHOR and _is_anonymous_name(clean_name):
                logger.info(
                    "Skipping anonymous author %r on manuscript (use P1574→work path)",
                    clean_name,
                )
                return

            if pid == "P195":
                return

            person_item = self._get_or_create_person(name, viaf_uri, mazal_id, role, record)
            resolved_qid = self._person_qids.get(key) or person_item.existing_qid

            # Guard: well-known institutional QIDs must never appear as P50.
            _INSTITUTIONAL_QIDS_BLOCKLIST = {Q_NLI}  # Q188915
            if pid == P_AUTHOR and resolved_qid in _INSTITUTIONAL_QIDS_BLOCKLIST:
                return

            # P50 (author) must NEVER appear directly on a manuscript item.
            # Person items are still created so authority IDs are not lost;
            # work projection attaches them via P1574 → work → P50.
            if pid == P_AUTHOR:
                logger.debug(
                    "Suppressing P50 on manuscript for %r (constraint: use P1574 path)",
                    name,
                )
                return

            if resolved_qid:
                item.statements.append(
                    WikidataStatement(
                        property_id=pid,
                        value=resolved_qid,
                        value_type="item",
                        references=ref,
                    )
                )
            elif not person_item.labels:
                logger.info("Skipping unresolved %s claim for %r", role, name)
                return
            else:
                item.statements.append(
                    WikidataStatement(
                        property_id=pid,
                        value=f"__LOCAL:{person_item.local_id}",
                        value_type="item",
                        references=ref,
                        qualifiers=[
                            {
                                "property": P_OBJECT_NAMED_AS,
                                "value": name.strip().rstrip(",;:"),
                                "type": "string",
                            },
                            {
                                "property": P_SOURCING_CIRCUMSTANCES,
                                "value": Q_PRESUMABLY,
                                "type": "item",
                            },
                        ],
                    )
                )

        # From MARC authority matches (structured name fields 100/700/etc.)
        for match in record.get("marc_authority_matches") or []:
            _add_person_statement(
                str(match.get("name", "")),
                str(match.get("role", "")),
                match.get("viaf_uri"),
                match.get("mazal_id"),
            )

        # From NER entities (extracted from note fields)
        for entity in record.get("entities") or []:
            _add_person_statement(
                str(entity.get("person", "")),
                str(entity.get("role", "")),
                entity.get("viaf_uri"),
                entity.get("mazal_id"),
            )
