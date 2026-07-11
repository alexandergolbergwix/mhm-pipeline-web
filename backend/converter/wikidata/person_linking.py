"""Role-aware person linking for manuscript projections."""

from __future__ import annotations

from converter.wikidata.item_builder import (
    P_AUTHOR,
    P_NATURE_OF_STATEMENT,
    P_OBJECT_HAS_ROLE,
    P_OBJECT_NAMED_AS,
    P_SOURCING_CIRCUMSTANCES,
    Q_AUTHOR_OCCUPATION,
    Q_HYPOTHESIS,
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

        Wikidata convention (per WikiProject Manuscripts):
        - P50 (author) belongs on WORK items, not manuscripts
        - P11603 (transcribed by) goes directly on manuscripts (scribes)
        - P127 (owned by) goes directly on manuscripts (owners)

        Authors are linked via P1574: MS → exemplar of → Work → P50 → Author.
        When no separate work item exists, P50 is placed on the MS as fallback.

        Entity linking flow:
        1. Person has VIAF URI → reconciler resolves to Wikidata QID → use QID
        2. Person has Mazal/NLI ID → reconciler resolves to Wikidata QID → use QID
        3. Person not found on Wikidata → create new person item with P214 + P8189
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
            role_norm = role.strip().lower()
            pid = ROLE_TO_PID.get(role_norm) or ROLE_TO_PID.get(role.upper())
            if pid is None:
                logger.warning("Skipping unsupported MARC/NER role %r for %r", role, name)
                return
            seen_person_keys.add(key)

            # Bug fix (2026-04-15, Geagea complaint on Q139085958): an
            # institutional contributor (MARC 710 "current owner" = National
            # Library of Israel, etc.) was being attached as P50 (author).
            # Institutions cannot be authors of manuscripts. Re-route them:
            #   - If pid would be P50 (author) AND the name is institutional,
            #     change pid to P195 (collection) instead.
            #   - "owner" / "current_owner" roles already map to P127 (owned
            #     by) which is correct.
            if pid == P_AUTHOR and _is_institutional_name(name):
                pid = "P195"  # collection
            # Rule 42 Phase 1 (HMO fidelity, 2026-05-17): known-anonymous
            # author. Rule 28 already blocks the creation of a person item
            # for placeholder names ("Anonymous", "לא ידוע", …). Instead of
            # dropping the signal or emitting only a flat P2093 string,
            # encode it as a manuscript-side P50 somevalue with role and
            # name-as-qualifier plus P5102=hypothesis. This makes the
            # assertion "this manuscript has an author, identity unknown"
            # machine-readable rather than silent.
            clean_name_anon = _strip_name_quotes(name)
            if pid == P_AUTHOR and _is_anonymous_name(clean_name_anon):
                item.statements.append(
                    WikidataStatement(
                        property_id=P_AUTHOR,
                        value=None,
                        value_type="somevalue",
                        qualifiers=[
                            {
                                "property": P_OBJECT_HAS_ROLE,
                                "value": Q_AUTHOR_OCCUPATION,
                                "type": "item",
                            },
                            {
                                "property": "P2093",
                                "value": clean_name_anon,
                                "type": "string",
                            },
                            {
                                "property": P_NATURE_OF_STATEMENT,
                                "value": Q_HYPOTHESIS,
                                "type": "item",
                            },
                        ],
                        references=ref,
                    )
                )
                return

            if pid == "P195":
                return

            person_item = self._get_or_create_person(name, viaf_uri, mazal_id, role, record)
            resolved_qid = self._person_qids.get(key) or person_item.existing_qid

            # Guard: well-known institutional QIDs must never appear as P50
            # (author). Q188915 = National Library of Israel — it was incorrectly
            # assigned as P50 in the April 2026 incident (Q139085958 had NLI as
            # 4 of its 5 author values). Re-route to P195 (collection).
            _INSTITUTIONAL_QIDS_BLOCKLIST = {Q_NLI}  # Q188915
            if pid == P_AUTHOR and resolved_qid in _INSTITUTIONAL_QIDS_BLOCKLIST:
                pid = "P195"  # collection

            # P50 (author) must NEVER appear directly on a manuscript item.
            # Wikidata constraint (2026-06-04 audit, confirmed on Property:P50):
            # "use exemplar of (P1574) to connect the manuscript to the work(s)
            # it contains; never connect directly the manuscript to the author(s)
            # of the work(s) it contains".
            # The correct data model is: manuscript → P1574 → work → P50 → author.
            # When we have a resolved QID for an author but no identified work
            # item, we suppress the direct P50 link rather than violate the
            # constraint. The person item IS still created with all its authority
            # identifiers (P214, P8189, P244…) so the entity is not lost.
            # Scribes (P11603), owners (P127), commissioners (P88), etc. are
            # direct-manuscript properties and are unaffected by this guard.
            if pid == P_AUTHOR and resolved_qid:
                logger.debug(
                    "Suppressing P50 → %s on manuscript (constraint: use P1574 path instead)",
                    resolved_qid,
                )
                return  # person item created above; do NOT add P50 on manuscript

            # For scribes/owners → direct claim on manuscript
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
                # Unresolved people cannot be safely represented as item-valued
                # manuscript claims. Keep the source in MARC/HMO evidence only.
                logger.info("Skipping unresolved %s claim for %r", role, name)
                return
            elif pid != P_AUTHOR:
                # Person identified via MARC/NER but not confirmed by authority
                # matching → add P1480 (presumably) to signal uncertain attribution.
                # This directly addresses the certainty/confidence mechanism
                # requested by domain experts (Lavee, Baumgarten, Univ. Haifa).
                # Excluded: P50 (author) — see constraint above; authors require
                # the P1574 → work → P50 path, never a direct manuscript P50.
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
