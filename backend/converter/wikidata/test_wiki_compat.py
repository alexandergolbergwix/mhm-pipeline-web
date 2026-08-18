"""Smoke-path helpers for test.wikidata.org claim writes (Rules W-182 / W-183).

test.wikidata.org reuses production P/Q numbers for unrelated properties, so a
WikiProject Manuscripts claim set cannot be written unchanged. On test uploads
we remap by English label + datatype (and stub-create when needed), then drop
only leftovers. Live uploads must not call this module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from converter.wikidata.item_models import WikidataItem, WikidataStatement

# Projection value_type → Wikibase `datatype` string from wbgetentities.
VALUE_TYPE_TO_WIKIBASE_DATATYPE: dict[str, str] = {
    "item": "wikibase-item",
    "wikibase-item": "wikibase-item",
    "wikibase-entityid": "wikibase-item",
    "string": "string",
    "external-id": "external-id",
    "time": "time",
    "quantity": "quantity",
    "url": "url",
    "monolingualtext": "monolingualtext",
    # WBI somevalue/novalue stubs are Item claims.
    "somevalue": "wikibase-item",
    "novalue": "wikibase-item",
}


@dataclass
class WikiTestAdaptStats:
    properties_remapped: int = 0
    classes_remapped: int = 0
    properties_created: int = 0
    classes_created: int = 0


@dataclass
class WikiTestAdaptResult:
    stats: WikiTestAdaptStats = field(default_factory=WikiTestAdaptStats)
    skipped: list[str] = field(default_factory=list)


def expected_wikibase_datatype(value_type: str) -> str | None:
    key = (value_type or "").strip().lower()
    return VALUE_TYPE_TO_WIKIBASE_DATATYPE.get(key)


def item_target_qid(value: object) -> str | None:
    text = str(value or "").strip()
    if text[:1] in {"Q", "q"} and text[1:].isdigit():
        return "Q" + text[1:]
    return None


def _entity_numeric_id(entity_id: str) -> int:
    text = (entity_id or "").strip()
    if len(text) > 1 and text[0] in {"P", "Q"} and text[1:].isdigit():
        return int(text[1:])
    return 999_999_999


def _snak_pid(snak: Mapping[str, object]) -> str:
    return str(snak.get("property") or snak.get("property_id") or "").strip()


def _snak_value_type(snak: Mapping[str, object]) -> str:
    return str(snak.get("value_type") or snak.get("type") or "string").strip()


def collect_live_pids_with_types(item: WikidataItem) -> list[tuple[str, str]]:
    """Return unique (live_pid, value_type) pairs from an item."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(pid: str, vtype: str) -> None:
        key = (pid, vtype)
        if pid and key not in seen:
            seen.add(key)
            out.append(key)

    def walk_snaks(snaks: Sequence[Mapping[str, object]] | None) -> None:
        for snak in snaks or []:
            add(_snak_pid(snak), _snak_value_type(snak))

    for stmt in item.statements:
        add(stmt.property_id, stmt.value_type)
        walk_snaks(stmt.qualifiers)
        walk_snaks(stmt.references)
    return out


def collect_test_wiki_ids(item: WikidataItem) -> tuple[list[str], list[str]]:
    """Return unique property ids and item QIDs referenced by the item."""
    pids: list[str] = []
    qids: list[str] = []
    seen_p: set[str] = set()
    seen_q: set[str] = set()

    def add_pid(pid: str) -> None:
        if pid and pid not in seen_p:
            seen_p.add(pid)
            pids.append(pid)

    def add_qid(qid: str | None) -> None:
        if qid and qid not in seen_q:
            seen_q.add(qid)
            qids.append(qid)

    def walk_snaks(snaks: Sequence[Mapping[str, object]] | None) -> None:
        for snak in snaks or []:
            add_pid(_snak_pid(snak))
            vtype = _snak_value_type(snak)
            if expected_wikibase_datatype(vtype) == "wikibase-item":
                add_qid(item_target_qid(snak.get("value")))

    for stmt in item.statements:
        add_pid(stmt.property_id)
        if expected_wikibase_datatype(stmt.value_type) == "wikibase-item":
            add_qid(item_target_qid(stmt.value))
        if stmt.unit:
            add_qid(item_target_qid(stmt.unit))
        walk_snaks(stmt.qualifiers)
        walk_snaks(stmt.references)
    return pids, qids


def collect_live_qids(item: WikidataItem) -> list[str]:
    """Return live Q-ids referenced as item values or quantity units."""
    _, qids = collect_test_wiki_ids(item)
    return qids


def choose_test_property(
    live_pid: str,
    value_type: str,
    *,
    property_label: str,
    property_datatypes: Mapping[str, str | None],
    pid_map: Mapping[str, str],
    search_hits: Sequence[Mapping[str, str]] | None = None,
) -> str | None:
    """Pick a test P-id for a live property, or None if create/skip is needed."""
    cached = pid_map.get(live_pid)
    if cached:
        return cached
    expected = expected_wikibase_datatype(value_type)
    if expected is None:
        return None
    actual_same = property_datatypes.get(live_pid)
    if actual_same == expected:
        return live_pid
    target_label = property_label.strip().lower()
    if not target_label:
        return None
    candidates: list[tuple[int, int, str]] = []
    for hit in search_hits or ():
        hit_pid = str(hit.get("id") or "").strip()
        hit_dt = str(hit.get("datatype") or "").strip()
        hit_label = str(hit.get("label") or "").strip().lower()
        if not hit_pid.startswith("P") or hit_dt != expected:
            continue
        exact_rank = 0 if hit_label == target_label else 1
        candidates.append((exact_rank, _entity_numeric_id(hit_pid), hit_pid))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


def choose_test_item(
    live_qid: str,
    *,
    item_label: str,
    qid_map: Mapping[str, str],
    search_hits: Sequence[Mapping[str, str]] | None = None,
) -> str | None:
    """Pick a test Q-id by gloss; never reuse live Q-id by number alone (W-183)."""
    cached = qid_map.get(live_qid)
    if cached:
        return cached
    target = item_label.strip().lower()
    if not target:
        return None
    candidates: list[tuple[int, str]] = []
    for hit in search_hits or ():
        hit_qid = str(hit.get("id") or "").strip()
        hit_label = str(hit.get("label") or "").strip().lower()
        if not hit_qid.startswith("Q") or hit_label != target:
            continue
        candidates.append((_entity_numeric_id(hit_qid), hit_qid))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def rewrite_item_with_maps(
    item: WikidataItem,
    pid_map: Mapping[str, str],
    qid_map: Mapping[str, str],
) -> WikidataItem:
    """Rewrite statement P/Q ids using session maps (pure)."""

    def map_pid(pid: str) -> str:
        return pid_map.get(pid, pid)

    def map_qid_value(value: object) -> object:
        qid = item_target_qid(value)
        if qid and qid in qid_map:
            return qid_map[qid]
        return value

    def map_value(vtype: str, value: object) -> object:
        if expected_wikibase_datatype(vtype) == "wikibase-item":
            return map_qid_value(value)
        return value

    def rewrite_snak(snak: Mapping[str, object]) -> dict[str, object]:
        new: dict[str, object] = dict(snak)
        pid = _snak_pid(snak)
        vtype = _snak_value_type(snak)
        new_pid = map_pid(pid)
        if new_pid != pid:
            new["property"] = new_pid
            if "property_id" in new:
                new["property_id"] = new_pid
        val = snak.get("value")
        new_val = map_value(vtype, val)
        if new_val != val:
            new["value"] = new_val
        return new

    kept: list[WikidataStatement] = []
    for stmt in item.statements:
        unit = stmt.unit
        if unit:
            mapped_unit = map_qid_value(unit)
            unit = str(mapped_unit) if mapped_unit is not None else unit
        kept.append(
            replace(
                stmt,
                property_id=map_pid(stmt.property_id),
                value=map_value(stmt.value_type, stmt.value),
                unit=unit if isinstance(unit, str) else stmt.unit,
                qualifiers=[rewrite_snak(q) for q in (stmt.qualifiers or [])],
                references=[rewrite_snak(r) for r in (stmt.references or [])],
            )
        )
    return replace(item, statements=kept)


def snak_compatible_with_test(
    *,
    property_id: str,
    value_type: str,
    value: object = None,
    property_datatypes: Mapping[str, str | None],
    existing_item_ids: set[str],
    live_static_qids: set[str] | None = None,
    allowed_item_ids: set[str] | None = None,
) -> str | None:
    """Return a skip reason, or None if the snak may be written on test."""
    pid = (property_id or "").strip()
    if not pid:
        return "empty property id"
    if pid not in property_datatypes:
        return f"{pid} datatype unknown on test"
    actual = property_datatypes[pid]
    if actual is None:
        return f"{pid} missing on test"
    expected = expected_wikibase_datatype(value_type)
    if expected is None:
        return f"{pid} unsupported value_type {value_type!r}"
    if actual != expected:
        return f"{pid} datatype {value_type} != {actual}"
    if expected == "wikibase-item" and (value_type or "").strip().lower() not in {
        "somevalue", "novalue",
    }:
        qid = item_target_qid(value)
        if qid:
            allowed = allowed_item_ids or set()
            if live_static_qids and qid in live_static_qids and qid not in allowed:
                return (
                    f"{pid} target {qid} is a live Q-id not remapped on test "
                    "(Rule W-183)"
                )
            if qid not in existing_item_ids:
                return f"{pid} target {qid} missing on test"
    return None


def filter_item_for_test_wiki(
    item: WikidataItem,
    *,
    property_datatypes: Mapping[str, str | None],
    existing_item_ids: set[str],
    live_static_qids: set[str] | None = None,
    allowed_item_ids: set[str] | None = None,
) -> tuple[WikidataItem, list[str]]:
    """Drop claims/snaks that still cannot be written after remap.

    Labels, descriptions, and aliases are kept. Live uploads must not use this.
    """
    skipped: list[str] = []
    kept: list[WikidataStatement] = []

    def filter_snaks(
        snaks: Sequence[Mapping[str, object]] | None,
    ) -> list:
        out: list = []
        for snak in snaks or []:
            reason = snak_compatible_with_test(
                property_id=_snak_pid(snak),
                value_type=_snak_value_type(snak),
                value=snak.get("value"),
                property_datatypes=property_datatypes,
                existing_item_ids=existing_item_ids,
                live_static_qids=live_static_qids,
                allowed_item_ids=allowed_item_ids,
            )
            if reason:
                skipped.append(reason)
                continue
            out.append(snak)
        return out

    for stmt in item.statements:
        reason = snak_compatible_with_test(
            property_id=stmt.property_id,
            value_type=stmt.value_type,
            value=stmt.value,
            property_datatypes=property_datatypes,
            existing_item_ids=existing_item_ids,
            live_static_qids=live_static_qids,
            allowed_item_ids=allowed_item_ids,
        )
        if reason:
            skipped.append(reason)
            continue
        kept.append(
            replace(
                stmt,
                qualifiers=filter_snaks(stmt.qualifiers),
                references=filter_snaks(stmt.references),
            )
        )
    return replace(item, statements=kept), skipped


def format_test_wiki_outcome_note(result: WikiTestAdaptResult | None) -> str:
    """Human-readable upload suffix for test.wikidata.org adapt results."""
    if result is None:
        return ""
    parts: list[str] = []
    stats = result.stats
    prop_n = stats.properties_remapped + stats.properties_created
    class_n = stats.classes_remapped + stats.classes_created
    if prop_n:
        parts.append(f"remapped {prop_n} properties")
    if class_n:
        parts.append(f"remapped {class_n} classes")
    if result.skipped:
        parts.append(f"skipped {len(result.skipped)} snaks")
    if not parts:
        return ""
    return "; " + ", ".join(parts) + " (Rule W-182/W-183)"
