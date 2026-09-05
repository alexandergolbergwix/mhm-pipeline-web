"""Merge rich legacy MARC Wikidata items into canonical HMO Studio items.

Rule W-125: canonical Studio default must carry the full research-grade
claim surface (production, contents, agents, housing, codicology,
provenance) while keeping HMO local_ids, bridges, and existing QIDs.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.pipeline.marc_verify_context import (
    canonical_control_number,
    primary_control_number_for,
)
from converter.wikidata.item_models import WikidataItem, WikidataStatement

logger = logging.getLogger(__name__)

# Prefer the canonical value when both sides emit the same PID.
# PIDs where the canonical projection wins outright: a legacy value is dropped
# when the canonical item already carries that property. For P31 this is
# essential — merging legacy types back in reintroduces the discouraged classes
# Rule W-98 forbids.
_CANONICAL_PREFERRED_PIDS = frozenset({
    "P31",
    "P2888",
    "P973",
})

# ...except that "already has this property" is the wrong test for a property
# that legitimately holds several values. P973 carries BOTH the HMO bridge and
# the catalogue's own record URL (MARC 856$u), so suppressing by PID silently
# dropped the catalogue link from all 68 manuscripts (Rule W-142). For these,
# only an identical VALUE is a duplicate.
_MULTI_VALUE_CANONICAL_PIDS = frozenset({"P973"})

_QID_RE = re.compile(r"^Q\d+$", re.IGNORECASE)
_LOCAL_REFERENCE_PREFIX = "__LOCAL:"


def _legacy_catalogue_p973_ok(canonical: WikidataItem, stmt: WikidataStatement) -> bool:
    """Drop cross-record catalogue P973 whose embedded ref ≠ P217 (W-174)."""
    from converter.wikidata.manuscript_projection import (  # noqa: PLC0415
        catalogue_url_agrees_with_shelfmark,
    )
    from converter.wikidata.property_mapping import (  # noqa: PLC0415
        is_browseable_hmo_wikibase_url,
    )

    url = str(stmt.value or "")
    if not url or is_browseable_hmo_wikibase_url(url):
        return True
    shelfmark = ""
    for existing in canonical.statements or []:
        if existing.property_id == "P217":
            shelfmark = str(existing.value or "")
            break
    return catalogue_url_agrees_with_shelfmark(url, shelfmark)


_PERSON_IDENTIFIER_PIDS = frozenset(
    {"P214", "P8189", "P244", "P227", "P213", "P268"},
)


def _has_publishable_person_identifier(item: WikidataItem) -> bool:
    """Keep only person items that can pass the final notability gate."""
    if str(item.existing_qid or "").strip():
        return True
    return any(
        str(statement.property_id or "") in _PERSON_IDENTIFIER_PIDS
        and str(statement.value or "").strip()
        for statement in item.statements or []
    )


def is_publishable_person_item(item: WikidataItem) -> bool:
    """Return whether a person has an existing QID or authority identifier."""
    return _has_publishable_person_identifier(item)


_UPLOAD_SKIP_MESSAGE = (
    "Not publishable — no VIAF/NLI/QID (Rule W-154/W-185)."
)

_HARD_REJECT_AUTHORITY_FLAGS = frozenset({
    "placeholder_name",
    "non_person_heading",
    "date_conflict",
    "biographical_inconsistency",
    "modern_person",
})
_SOFT_REJECT_AUTHORITY_FLAGS = frozenset({"wikidata_crosscheck_fail"})


def person_identity_untrusted_for_recovery(item: WikidataItem) -> bool:
    """True when W-166 flags forbid recovering identifiers from evidence."""
    if getattr(item, "heading_mismatch", None):
        return True
    flags: set[str] = set()
    for row in getattr(item, "authority_evidence", None) or []:
        if isinstance(row, dict):
            flags |= {str(f) for f in (row.get("guard_flags") or [])}
    return bool(flags & (_HARD_REJECT_AUTHORITY_FLAGS | _SOFT_REJECT_AUTHORITY_FLAGS))


def recover_person_identifiers_from_evidence(item: WikidataItem) -> bool:
    """Stamp P214/P8189/existing_qid from trusted evidence when W-166 allows.

    Runs before the W-154 omit gate (Rule W-188). ``accepted is False`` is
    never recovered. Missing ``accepted`` is usable only when the item is
    not W-166-untrusted. Legacy ``viaf_uri``/``mazal_id``/``wikidata_qid``
    rows are stamped per kind, not first-wins across mixed fields.
    """
    if str(item.entity_type or "").strip().lower() != "person":
        return False
    if person_identity_untrusted_for_recovery(item):
        return False
    if is_publishable_person_item(item):
        return False

    from converter.authority.evidence import (  # noqa: PLC0415
        evidence_row_ids,
        normalize_authority_id,
        normalize_viaf_id,
        normalize_wikidata_qid,
    )

    changed = False
    seen_pids = {
        str(stmt.property_id or "")
        for stmt in item.statements or []
    }

    for row in getattr(item, "authority_evidence", None) or []:
        if not isinstance(row, dict) or row.get("accepted") is False:
            continue
        ids = evidence_row_ids(row)
        if not ids:
            continue
        qid = normalize_wikidata_qid(ids.get("wikidata") or "")
        if qid and not item.existing_qid:
            item.existing_qid = qid
            changed = True
        if "P214" not in seen_pids and ids.get("viaf"):
            name_type = str(row.get("name_type") or "")
            if name_type and name_type != "Personal":
                pass
            else:
                viaf = normalize_viaf_id(ids["viaf"])
                if viaf:
                    item.statements = list(item.statements or [])
                    item.statements.append(
                        WikidataStatement(
                            property_id="P214",
                            value=viaf,
                            value_type="external-id",
                        ),
                    )
                    seen_pids.add("P214")
                    changed = True
        if "P8189" not in seen_pids and ids.get("mazal"):
            mazal = normalize_authority_id(ids["mazal"]) or ids["mazal"]
            if mazal.startswith("9870"):
                item.statements = list(item.statements or [])
                item.statements.append(
                    WikidataStatement(
                        property_id="P8189",
                        value=mazal,
                        value_type="external-id",
                    ),
                )
                seen_pids.add("P8189")
                changed = True

    return changed


def _person_display_name(item: WikidataItem) -> str:
    labels = item.labels or {}
    return str(labels.get("he") or labels.get("en") or "").strip()


def rollup_dropped_person_local_refs(
    items: list[WikidataItem],
    dropped_local_ids: set[str],
    *,
    names_by_local_id: dict[str, str],
) -> int:
    """Rewrite work P50 ``__LOCAL:<dropped>`` to P2093 author name strings."""
    from converter.wikidata.property_mapping import P_AUTHOR_NAME_STRING  # noqa: PLC0415

    rolled = 0
    for item in items:
        if str(item.entity_type or "").strip().lower() != "work":
            continue
        kept: list[WikidataStatement] = []
        for stmt in item.statements or []:
            pid = str(stmt.property_id or "")
            value = str(stmt.value or "")
            if pid != "P50" or not value.startswith("__LOCAL:"):
                kept.append(stmt)
                continue
            local_id = value.removeprefix("__LOCAL:")
            if local_id not in dropped_local_ids:
                kept.append(stmt)
                continue
            name = names_by_local_id.get(local_id, "").strip()
            if not name:
                continue
            kept.append(
                WikidataStatement(
                    property_id=P_AUTHOR_NAME_STRING,
                    value=name,
                    value_type="string",
                ),
            )
            rolled += 1
        item.statements = kept
    return rolled


def _person_strong_identity_keys(item: WikidataItem) -> set[tuple[str, str]]:
    """Return the strong authority IDs that can identify one person draft."""
    from converter.authority.evidence import (  # noqa: PLC0415
        normalize_authority_id,
        normalize_viaf_id,
    )

    identities: set[tuple[str, str]] = set()
    for statement in item.statements or []:
        property_id = str(statement.property_id or "")
        value = str(statement.value or "").strip()
        if property_id not in _PERSON_IDENTIFIER_PIDS or not value:
            continue
        if property_id == "P214":
            value = normalize_viaf_id(value) or value.rstrip("/").rsplit("/", 1)[-1]
        elif property_id == "P8189":
            value = normalize_authority_id(value) or value
        identities.add((property_id, value))
    return identities


def coalesce_person_items_by_strong_identity(
    items: list[WikidataItem],
) -> tuple[list[WikidataItem], int]:
    """Merge same-person drafts that share a strong ID (Rule W-213).

    Different existing QIDs remain separate. The final collision gate then
    blocks that unsafe corpus instead of silently choosing a public item.
    """
    people_by_local_id: dict[str, WikidataItem] = {}
    members_by_identity: dict[tuple[str, str], set[str]] = {}
    for item in items:
        if str(item.entity_type or "").strip().lower() != "person":
            continue
        local_id = str(item.local_id or "").strip()
        if not local_id or local_id in people_by_local_id:
            continue
        people_by_local_id[local_id] = item
        for identity in _person_strong_identity_keys(item):
            members_by_identity.setdefault(identity, set()).add(local_id)

    adjacent: dict[str, set[str]] = {
        local_id: set() for local_id in people_by_local_id
    }
    for member_ids in members_by_identity.values():
        if len(member_ids) < 2:
            continue
        for local_id in member_ids:
            adjacent[local_id].update(member_ids - {local_id})

    replacements: dict[str, WikidataItem] = {}
    aliases: dict[str, str] = {}
    removed_ids: set[str] = set()
    visited: set[str] = set()
    for local_id in sorted(adjacent):
        if local_id in visited:
            continue
        stack = [local_id]
        component: set[str] = set()
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.add(current)
            stack.extend(adjacent[current] - visited)
        if len(component) < 2:
            continue
        existing_qids = {
            str(people_by_local_id[member].existing_qid or "").strip()
            for member in component
            if str(people_by_local_id[member].existing_qid or "").strip()
        }
        if len(existing_qids) > 1:
            logger.warning(
                "Kept %d person drafts with shared strong IDs because they target "
                "conflicting QIDs: %s",
                len(component),
                ", ".join(sorted(existing_qids)),
            )
            continue

        survivor_id = min(
            component,
            key=lambda member: (
                not bool(str(people_by_local_id[member].existing_qid or "").strip()),
                member,
            ),
        )
        merged = people_by_local_id[survivor_id]
        for duplicate_id in sorted(component - {survivor_id}):
            merged = _merge_pair(merged, people_by_local_id[duplicate_id])
            aliases[duplicate_id] = survivor_id
            removed_ids.add(duplicate_id)
        replacements[survivor_id] = merged

    if not removed_ids:
        return items, 0

    coalesced = [
        replacements.get(str(item.local_id or ""), item)
        for item in items
        if str(item.local_id or "") not in removed_ids
    ]
    rewritten = _rewrite_local_reference_aliases(coalesced, aliases)
    logger.info(
        "Coalesced %d person drafts by shared strong identity and rewrote %d "
        "local references (Rule W-213)",
        len(removed_ids),
        rewritten,
    )
    return _with_deduped_statements(coalesced), len(removed_ids)


def prepare_wikidata_upload_native_items(items: list[WikidataItem]) -> list[WikidataItem]:
    """Recover IDs, coalesce people, omit identifierless persons, and rollup P2093."""
    for item in items:
        if str(item.entity_type or "").strip().lower() == "person":
            recover_person_identifiers_from_evidence(item)

    items, _ = coalesce_person_items_by_strong_identity(items)

    dropped_ids: set[str] = set()
    names: dict[str, str] = {}
    for item in items:
        if str(item.entity_type or "").strip().lower() != "person":
            continue
        if is_publishable_person_item(item):
            continue
        lid = str(item.local_id or "")
        if not lid:
            continue
        dropped_ids.add(lid)
        names[lid] = _person_display_name(item)

    if not dropped_ids:
        return items

    kept = [
        item for item in items
        if str(item.local_id or "") not in dropped_ids
    ]
    rollup_dropped_person_local_refs(kept, dropped_ids, names_by_local_id=names)
    logger.info(
        "Omitted %d identifierless persons from upload set (Rule W-185): %s",
        len(dropped_ids),
        ", ".join(sorted(dropped_ids)),
    )
    return kept


def _keep_merged_item(item: WikidataItem) -> bool:
    if str(item.entity_type or "").strip().lower() != "person":
        return True
    recover_person_identifiers_from_evidence(item)
    return is_publishable_person_item(item)


def merge_legacy_into_canonical(
    canonical_items: list[WikidataItem],
    legacy_items: list[WikidataItem],
) -> list[WikidataItem]:
    """Enrich canonical items with legacy MARC/authority claims.

    - Keeps every canonical ``local_id`` / HMO bridge / ``existing_qid``.
    - Unions statements from the best-matching legacy item.
    - Appends unmatched legacy persons/works (already fail-closed on IDs).
    - Does not append unmatched legacy manuscripts (canonical is the MS root).
    """
    if not legacy_items:
        return _with_deduped_statements(
            [item for item in canonical_items if _keep_merged_item(item)],
        )
    if not canonical_items:
        return _with_deduped_statements(
            [item for item in legacy_items if _keep_merged_item(item)],
        )

    legacy_ms = _index_manuscripts(legacy_items)
    legacy_persons = _index_persons(legacy_items)
    legacy_works = _index_works(legacy_items)
    used_legacy_ids: set[str] = set()
    local_id_aliases: dict[str, str] = {}

    merged: list[WikidataItem] = []
    for item in canonical_items:
        et = (item.entity_type or "").strip().lower()
        legacy: WikidataItem | None = None
        if et == "manuscript":
            legacy = _match_manuscript(item, legacy_ms)
        elif et == "person":
            legacy = _match_person(item, legacy_persons)
        elif et == "work":
            legacy = _match_work(item, legacy_works)
        if legacy is not None:
            used_legacy_ids.add(legacy.local_id or id(legacy).__repr__())
            candidate = _merge_pair(item, legacy)
            legacy_local_id = str(legacy.local_id or "")
            canonical_local_id = str(candidate.local_id or "")
            if (
                legacy_local_id
                and canonical_local_id
                and legacy_local_id != canonical_local_id
            ):
                local_id_aliases[legacy_local_id] = canonical_local_id
            if _keep_merged_item(candidate):
                merged.append(candidate)
        else:
            item.statements = dedupe_statements(item.statements)
            candidate = _with_canonical_titles(_with_scoped_records(item))
            if _keep_merged_item(candidate):
                merged.append(candidate)

    for legacy in legacy_items:
        lid = legacy.local_id or ""
        key = lid or id(legacy).__repr__()
        if key in used_legacy_ids:
            continue
        et = (legacy.entity_type or "").strip().lower()
        if et == "manuscript":
            # Canonical already owns the manuscript public item for each CN.
            continue
        if et in {"person", "work"}:
            legacy.statements = dedupe_statements(legacy.statements)
            candidate = _with_canonical_titles(legacy)
            if _keep_merged_item(candidate):
                merged.append(candidate)
    rewritten = _rewrite_local_reference_aliases(merged, local_id_aliases)
    if rewritten:
        logger.info(
            "Rewrote %d merged local references to canonical item IDs (Rule W-203)",
            rewritten,
        )
    normalize_work_author_claims(merged)
    return _with_deduped_statements(merged)


def _resolve_local_alias(local_id: str, aliases: dict[str, str]) -> str:
    """Follow merged local IDs until the canonical ID is reached."""
    resolved = local_id
    seen: set[str] = set()
    while resolved in aliases and resolved not in seen:
        seen.add(resolved)
        next_id = str(aliases[resolved] or "")
        if not next_id:
            break
        resolved = next_id
    return resolved


def _rewrite_local_reference_value(
    value: Any,
    aliases: dict[str, str],
) -> tuple[Any, int]:
    """Rewrite local IDs inside statement metadata after an item merge."""
    if isinstance(value, str) and value.startswith(_LOCAL_REFERENCE_PREFIX):
        local_id = value.removeprefix(_LOCAL_REFERENCE_PREFIX)
        resolved = _resolve_local_alias(local_id, aliases)
        if resolved != local_id:
            return f"{_LOCAL_REFERENCE_PREFIX}{resolved}", 1
        return value, 0
    if isinstance(value, dict):
        rewritten = 0
        for key, nested in value.items():
            replacement, count = _rewrite_local_reference_value(nested, aliases)
            value[key] = replacement
            rewritten += count
        return value, rewritten
    if isinstance(value, list):
        rewritten = 0
        for index, nested in enumerate(value):
            replacement, count = _rewrite_local_reference_value(nested, aliases)
            value[index] = replacement
            rewritten += count
        return value, rewritten
    return value, 0


def _rewrite_local_reference_aliases(
    items: list[WikidataItem],
    aliases: dict[str, str],
) -> int:
    """Point merged claims and metadata at the canonical item IDs (Rule W-203)."""
    if not aliases:
        return 0
    rewritten = 0
    for item in items:
        for statement in item.statements or []:
            replacement, count = _rewrite_local_reference_value(
                statement.value,
                aliases,
            )
            statement.value = replacement
            rewritten += count
            for metadata in (statement.qualifiers, statement.references):
                _, count = _rewrite_local_reference_value(metadata, aliases)
                rewritten += count
    return rewritten


def _merge_pair(canonical: WikidataItem, legacy: WikidataItem) -> WikidataItem:
    out = WikidataItem(
        labels=dict(canonical.labels or {}),
        descriptions=dict(canonical.descriptions or {}),
        aliases={
            lang: list(values)
            for lang, values in (canonical.aliases or {}).items()
        },
        statements=list(canonical.statements or []),
        existing_qid=canonical.existing_qid or legacy.existing_qid,
        entity_type=canonical.entity_type or legacy.entity_type,
        semantic_type=canonical.semantic_type or legacy.semantic_type,
        local_id=canonical.local_id,
        records=_merged_records(canonical, legacy),
        authority_evidence=(
            _union_evidence(
                canonical.authority_evidence, legacy.authority_evidence,
            )
        ),
        work_candidate_evidence=_union_work_evidence(
            canonical.work_candidate_evidence, legacy.work_candidate_evidence,
            allowed_records=set(_merged_records(canonical, legacy)),
        ),
    )
    # Prefer non-empty legacy labels/descriptions when canonical is thin.
    for lang, label in (legacy.labels or {}).items():
        text = str(label or "").strip()
        if text and not str(out.labels.get(lang) or "").strip():
            out.labels[lang] = text
    for lang, desc in (legacy.descriptions or {}).items():
        text = str(desc or "").strip()
        if text and not str(out.descriptions.get(lang) or "").strip():
            out.descriptions[lang] = text
    for lang, aliases in (legacy.aliases or {}).items():
        bucket = out.aliases.setdefault(lang, [])
        for alias in aliases or []:
            text = str(alias or "").strip()
            if text and text not in bucket:
                bucket.append(text)

    seen = {_statement_key(stmt) for stmt in out.statements}
    for stmt in legacy.statements or []:
        pid = str(stmt.property_id or "")
        key = _statement_key(stmt)
        if key in seen:
            continue
        if (
            pid in _CANONICAL_PREFERRED_PIDS
            and pid not in _MULTI_VALUE_CANONICAL_PIDS
            and _has_pid(out.statements, pid)
        ):
            continue
        if pid == "P973" and not _legacy_catalogue_p973_ok(out, stmt):
            continue
        out.statements.append(stmt)
        seen.add(key)
    out.statements = dedupe_statements(out.statements)
    _with_canonical_titles(out)
    _apply_printed_facsimile_typing(out, legacy)
    # Alias union can reintroduce 500-note work titles; re-apply W-176.
    from converter.wikidata.manuscript_metadata import (  # noqa: PLC0415
        _restrict_aliases_under_designation,
    )
    primary = ""
    for stmt in out.statements or []:
        if stmt.property_id == "P1476" and stmt.value:
            primary = str(stmt.value)
            break
    _restrict_aliases_under_designation(out, primary_title=primary)
    return out


def _apply_printed_facsimile_typing(
    merged: WikidataItem, legacy: WikidataItem,
) -> None:
    """A printed facsimile must not stay typed as a manuscript (Rule W-142).

    Only the legacy projection reads MARC 500$a `דפוס צלום`, and P31 is
    canonical-preferred, so the canonical `Q87167` overwrote the correct
    `Q571` and the item claimed to be a manuscript it is a reproduction of.
    """
    if (legacy.semantic_type or "") != "printed_facsimile":
        return
    merged.semantic_type = "printed_facsimile"
    kept = [
        stmt for stmt in merged.statements
        if not (str(stmt.property_id) == "P31" and str(stmt.value) == "Q87167")
    ]
    if not any(
        str(stmt.property_id) == "P31" and str(stmt.value) == "Q571" for stmt in kept
    ):
        printed = next(
            (
                stmt for stmt in (legacy.statements or [])
                if str(stmt.property_id) == "P31" and str(stmt.value) == "Q571"
            ),
            None,
        )
        if printed is not None:
            kept.append(printed)
    merged.statements = kept


def _with_scoped_records(item: WikidataItem) -> WikidataItem:
    """A manuscript item's MARC scope is its own record (Rule W-137)."""
    if (item.entity_type or "").strip().lower() == "manuscript":
        cn = primary_control_number_of(item)
        item.records = [cn] if cn else []
    return item


def _with_canonical_titles(item: WikidataItem) -> WikidataItem:
    """Keep one P1476 per work (Rule W-138)."""
    if (item.entity_type or "").strip().lower() != "work":
        return item
    keep = {id(statement) for statement in canonical_work_titles(item)}
    item.statements = [
        statement for statement in item.statements or []
        if str(statement.property_id or "") != "P1476" or id(statement) in keep
    ]
    return item


def _with_deduped_statements(items: list[WikidataItem]) -> list[WikidataItem]:
    for item in items:
        item.statements = dedupe_statements(item.statements)
        _with_scoped_records(item)
        _with_canonical_titles(item)
    return list(items)


def _is_safe_work_author_item(
    statement: WikidataStatement,
    person_local_ids: set[str],
) -> bool:
    """Return whether a work P50 target can replace a P2093 fallback."""
    value = str(statement.value or "").strip()
    if _QID_RE.fullmatch(value):
        return True
    return value.startswith(_LOCAL_REFERENCE_PREFIX) and (
        value.removeprefix(_LOCAL_REFERENCE_PREFIX) in person_local_ids
    )


def _merge_statement_metadata(
    retained: WikidataStatement,
    removed: WikidataStatement,
) -> None:
    """Keep source metadata when one representation of an author disappears."""
    for attribute in ("qualifiers", "references"):
        current = list(getattr(retained, attribute) or [])
        for value in getattr(removed, attribute) or []:
            if value not in current:
                current.append(value)
        setattr(retained, attribute, current)


def normalize_work_author_claims(items: list[WikidataItem]) -> int:
    """Keep one safe author representation per work (Rule W-204).

    Canonical and legacy projections can describe the same author as P50 and
    P2093. A safe P50 wins. An unresolved P50 yields to P2093 so the work keeps
    its source-backed author signal instead of losing it during local resolution.
    """
    person_local_ids = {
        str(item.local_id or "")
        for item in items
        if str(item.entity_type or "").strip().lower() == "person"
        and str(item.local_id or "").strip()
    }
    changed = 0
    for item in items:
        if str(item.entity_type or "").strip().lower() != "work":
            continue
        p50 = [
            statement for statement in item.statements or []
            if str(statement.property_id or "") == "P50"
        ]
        p2093 = [
            statement for statement in item.statements or []
            if str(statement.property_id or "") == "P2093"
        ]
        if not p50 or not p2093:
            continue

        safe_p50 = [
            statement
            for statement in p50
            if _is_safe_work_author_item(statement, person_local_ids)
        ]
        if safe_p50:
            retained = safe_p50[0]
            for removed in p2093:
                _merge_statement_metadata(retained, removed)
            item.statements = [
                statement
                for statement in item.statements
                if str(statement.property_id or "") != "P2093"
            ]
            changed += len(p2093)
            continue

        retained = p2093[0]
        for removed in p50:
            _merge_statement_metadata(retained, removed)
        item.statements = [
            statement
            for statement in item.statements
            if str(statement.property_id or "") != "P50"
        ]
        changed += len(p50)

    if changed:
        logger.info(
            "Normalized %d duplicate work author claims; safe P50 wins (Rule W-204)",
            changed,
        )
    return changed


def dedupe_statements(statements: list[WikidataStatement]) -> list[WikidataStatement]:
    """One statement per (property, value), keeping the best-sourced instance.

    ``_statement_key`` includes ``value_type``, so the same fact emitted as
    ``string`` and ``external-id`` survived twice — every manuscript shipped
    P3959 four or five times (Rule W-137). Ranking by (references, qualifiers)
    keeps the referenced instance the judge and curator want to see.
    """
    best: dict[tuple[str, str], WikidataStatement] = {}
    order: list[tuple[str, str]] = []
    for stmt in statements or []:
        key = (
            str(stmt.property_id or ""),
            str(stmt.value if stmt.value is not None else ""),
        )
        current = best.get(key)
        if current is None:
            best[key] = stmt
            order.append(key)
            continue
        rank = (len(stmt.references or []), len(stmt.qualifiers or []))
        current_rank = (len(current.references or []), len(current.qualifiers or []))
        if rank > current_rank:
            best[key] = stmt
    return [best[key] for key in order]


def _statement_key(stmt: WikidataStatement) -> tuple[str, str, str]:
    return (
        str(stmt.property_id or ""),
        str(stmt.value_type or ""),
        str(stmt.value if stmt.value is not None else ""),
    )


def _has_pid(statements: list[WikidataStatement], pid: str) -> bool:
    return any(str(s.property_id or "") == pid for s in statements)


def _union_records(a: list[str] | None, b: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in list(a or []) + list(b or []):
        cn = canonical_control_number(raw)
        if cn and cn not in seen:
            seen.add(cn)
            out.append(cn)
    return out


def _union_evidence(
    a: list[dict[str, object]] | None,
    b: list[dict[str, object]] | None,
) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in list(a or []) + list(b or []):
        if not isinstance(row, dict):
            continue
        key = "|".join(
            str(row.get(k) or "")
            for k in ("kind", "source", "viaf_uri", "mazal_id", "wikidata_qid", "identifier")
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(row))
    return out


def _union_work_evidence(
    a: Any,
    b: Any,
    *,
    allowed_records: set[str] | None = None,
) -> list[dict[str, object]]:
    """Union work evidence, keyed by the record each row came from.

    Deduping on the title alone silently kept whichever row came first when two
    records attested the same title, and nothing dropped a row whose record was
    not among the merged item's own — which is exactly how ``QDraft_Work_37``
    carried evidence from a record it does not cite (Rule W-165).
    """
    rows_a = a if isinstance(a, list) else ([a] if isinstance(a, dict) and a else [])
    rows_b = b if isinstance(b, list) else ([b] if isinstance(b, dict) and b else [])
    out: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in list(rows_a) + list(rows_b):
        if not isinstance(row, dict):
            continue
        record_id = str(row.get("source_record_id") or "")
        if allowed_records is not None and record_id and record_id not in allowed_records:
            continue
        key = (
            str(row.get("title") or row.get("source_text") or ""),
            record_id,
            str(row.get("source_field") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(row))
    return out


def _control_numbers_of(item: WikidataItem) -> set[str]:
    out: set[str] = set()
    for raw in item.records or []:
        cn = canonical_control_number(raw)
        if cn:
            out.add(cn)
    for stmt in item.statements or []:
        if str(stmt.property_id or "") == "P3959":
            cn = canonical_control_number(stmt.value)
            if cn:
                out.add(cn)
    return out


def _merged_records(canonical: WikidataItem, legacy: WikidataItem) -> list[str]:
    """Manuscripts keep only their own record; other types union sources.

    A manuscript's MARC slice is merged across its ``records``, so keeping
    linked CNs here feeds the judge (and the label/description builders) another
    manuscript's title, shelfmark and dates (Rule W-137). Persons and works
    legitimately span records — Rule W-63.
    """
    if (canonical.entity_type or "").strip().lower() == "manuscript":
        cn = primary_control_number_of(canonical) or primary_control_number_of(legacy)
        return [cn] if cn else []
    return _union_records(canonical.records, legacy.records)


def primary_control_number_of(item: WikidataItem) -> str:
    """The CN a manuscript item *is*, not the ones it merely links to."""
    return primary_control_number_for(
        sorted(_control_numbers_of(item)),
        item.local_id,
    )


def _index_manuscripts(items: list[WikidataItem]) -> dict[str, WikidataItem]:
    """Index legacy manuscripts by their OWN control number only.

    Indexing by every linked CN made several canonical manuscripts match the
    same legacy item, so each inherited that item's shelfmark, title, dates and
    holder — three distinct manuscripts shipped as `The British Library, F 8298`
    with `P217 = F 7956` (Rule W-137).
    """
    index: dict[str, WikidataItem] = {}
    for item in items:
        if (item.entity_type or "").lower() != "manuscript":
            continue
        cn = primary_control_number_of(item)
        if cn:
            index.setdefault(cn, item)
    return index


def _match_manuscript(
    item: WikidataItem,
    index: dict[str, WikidataItem],
) -> WikidataItem | None:
    cn = primary_control_number_of(item)
    if not cn:
        return None
    return index.get(cn)


def _person_keys(item: WikidataItem) -> set[str]:
    keys: set[str] = set()
    for stmt in item.statements or []:
        pid = str(stmt.property_id or "")
        value = str(stmt.value or "").strip()
        if pid == "P214" and value:
            keys.add(f"viaf:{value}")
        if pid == "P8189" and value:
            keys.add(f"mazal:{value}")
        if pid in {"P214", "P8189"}:
            continue
        if _QID_RE.fullmatch(value) and item.existing_qid and value == item.existing_qid:
            keys.add(f"qid:{value}")
    if item.existing_qid:
        keys.add(f"qid:{item.existing_qid}")
    for lang in ("he", "en"):
        label = str((item.labels or {}).get(lang) or "").strip().casefold()
        if label:
            keys.add(f"label:{label}")
    for row in getattr(item, "authority_evidence", None) or []:
        if not isinstance(row, dict):
            continue
        viaf = str(row.get("viaf_uri") or row.get("viaf_id") or "").strip()
        if viaf:
            viaf = viaf.rstrip("/").rsplit("/", 1)[-1]
            keys.add(f"viaf:{viaf}")
        mazal = str(row.get("mazal_id") or "").strip()
        if mazal:
            keys.add(f"mazal:{mazal}")
        qid = str(row.get("wikidata_qid") or "").strip()
        if qid:
            keys.add(f"qid:{qid}")
    return keys


def _index_persons(items: list[WikidataItem]) -> dict[str, WikidataItem]:
    index: dict[str, WikidataItem] = {}
    for item in items:
        if (item.entity_type or "").lower() != "person":
            continue
        for key in _person_keys(item):
            index.setdefault(key, item)
    return index


def _person_identifier_pairs(item: WikidataItem) -> set[tuple[str, str]]:
    """``(pid, value)`` for every publishable identifier on a person."""
    pairs: set[tuple[str, str]] = set()
    for stmt in item.statements or []:
        pid = str(stmt.property_id or "")
        value = str(stmt.value or "").strip()
        if pid in _PERSON_IDENTIFIER_PIDS and value:
            pairs.add((pid, value))
    return pairs


def _person_match_is_compatible(canonical: WikidataItem, legacy: WikidataItem) -> bool:
    """May a LABEL-keyed person match merge?

    Identifier and QID keys are exact and need no test. A label match does: two
    people can share a heading and be different people — that is the whole homonym
    problem (Rule W-37) — and merging them unions one identity into another.

    This guard was added after Rule W-166 changed which label a mismatched
    authority heading lands in. That quietly loosened label-keyed person matching:
    54 persons vanished from run 48ba6c13's rebuild, 39 absorbed into another
    person and **15 taking their only publishable identifier with them**, because
    ``_merge_pair`` keeps the canonical side's statements. A corpus-wide matching
    change is exactly what Rule W-166 gated behind a flag; it must not arrive
    through a label key instead.
    """
    if canonical.existing_qid and canonical.existing_qid == legacy.existing_qid:
        return True
    if canonical.local_id and canonical.local_id == legacy.local_id:
        return True
    left, right = _person_identifier_pairs(canonical), _person_identifier_pairs(legacy)
    if left & right:
        return True
    # Both sides carry identifiers and they disagree: different people.
    if left and right:
        return False
    # One side has none — a stub merging into an identified person is the case
    # this merge exists for.
    return True


def _match_person(
    item: WikidataItem,
    index: dict[str, WikidataItem],
) -> WikidataItem | None:
    for key in _person_keys(item):
        hit = index.get(key)
        if hit is None:
            continue
        if key.startswith("label:") and not _person_match_is_compatible(item, hit):
            continue
        return hit
    return None


def _work_keys(item: WikidataItem) -> set[str]:
    """Identity keys for a work.

    ``P1476`` values are deliberately NOT keys: an item can legitimately carry
    several title forms, so matching on any of them merged unrelated works — the
    Carpentras siddur inherited `סדר אליהו זוטא` as a second P1476 that no
    channel supported (Rule W-138). Identity is the QID, the local id, or the
    item's own label.
    """
    keys: set[str] = set()
    for lang in ("he", "en"):
        label = str((item.labels or {}).get(lang) or "").strip().casefold()
        if label:
            keys.add(f"label:{label}")
    if item.existing_qid:
        keys.add(f"qid:{item.existing_qid}")
    if item.local_id:
        keys.add(f"local:{item.local_id}")
    return keys


def canonical_work_titles(item: WikidataItem) -> list[WikidataStatement]:
    """One P1476 per work: the form that matches the work's own label.

    Works shipped up to three P1476 values — a quote-wrapped raw form, the clean
    form, and (via the old title-key merge) another work's title. A title claim
    that matches no label/alias of this item is not this work's title
    (Rule W-138).
    """
    from converter.rdf.rdf_helpers import sanitize_work_title  # noqa: PLC0415

    def norm(text: Any) -> str:
        cleaned = sanitize_work_title(str(text or ""))
        return cleaned.casefold().strip(" .,;:/-\"'")

    own = {norm(v) for v in (item.labels or {}).values() if v}
    for values in (item.aliases or {}).values():
        own.update(norm(v) for v in values or [] if v)
    own.discard("")

    titles = [s for s in item.statements or [] if str(s.property_id or "") == "P1476"]
    if len(titles) <= 1:
        return titles
    matching = [s for s in titles if norm(s.value) in own]
    if not matching:
        return titles[:1]
    best: dict[str, WikidataStatement] = {}
    for statement in matching:
        best.setdefault(norm(statement.value), statement)
    return list(best.values())


def _index_works(items: list[WikidataItem]) -> dict[str, WikidataItem]:
    index: dict[str, WikidataItem] = {}
    for item in items:
        if (item.entity_type or "").lower() != "work":
            continue
        for key in _work_keys(item):
            index.setdefault(key, item)
    return index


def _work_evidence_pairs(item: WikidataItem) -> set[tuple[str, str]]:
    """Accepted ``(title, source_record_id)`` pairs on a work."""
    pairs: set[tuple[str, str]] = set()
    for row in getattr(item, "work_candidate_evidence", None) or []:
        if isinstance(row, dict):
            pairs.add((
                str(row.get("title") or "").casefold(),
                str(row.get("source_record_id") or ""),
            ))
    return pairs


def _work_match_is_compatible(canonical: WikidataItem, legacy: WikidataItem) -> bool:
    """May a label-keyed work match merge?

    QID and local-id keys are exact and need no test. A LABEL match does: two
    works can share a label and be attested from unrelated records, and merging
    them unions both their records and their evidence — how ``QDraft_Work_37``
    came to carry a third record's evidence rows (Rule W-165).

    The block is targeted at that case, not at every label match: two works that
    each name records and name *disjoint* ones are different works. Recordless
    works still merge, because refusing would just mint a duplicate.
    """
    if canonical.existing_qid and canonical.existing_qid == legacy.existing_qid:
        return True
    if canonical.local_id and canonical.local_id == legacy.local_id:
        return True
    left, right = _control_numbers_of(canonical), _control_numbers_of(legacy)
    if left and right and not (left & right):
        # Both are anchored, to different records. Corroborated evidence can still
        # justify the merge; nothing else can.
        return bool(_work_evidence_pairs(canonical) & _work_evidence_pairs(legacy))
    return True


def _match_work(
    item: WikidataItem,
    index: dict[str, WikidataItem],
) -> WikidataItem | None:
    for key in _work_keys(item):
        hit = index.get(key)
        if hit is None:
            continue
        if key.startswith("label:") and not _work_match_is_compatible(item, hit):
            continue
        return hit
    return None
