"""Smoke-path helpers for test.wikidata.org claim writes (Rule W-182).

test.wikidata.org reuses production P/Q numbers for unrelated properties, so a
WikiProject Manuscripts claim set cannot be written unchanged. Live uploads
must not call this module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

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


def expected_wikibase_datatype(value_type: str) -> str | None:
    key = (value_type or "").strip().lower()
    return VALUE_TYPE_TO_WIKIBASE_DATATYPE.get(key)


def item_target_qid(value: object) -> str | None:
    text = str(value or "").strip()
    if text[:1] in {"Q", "q"} and text[1:].isdigit():
        return "Q" + text[1:]
    return None


def _snak_pid(snak: Mapping[str, object]) -> str:
    return str(snak.get("property") or "").strip()


def _snak_value_type(snak: Mapping[str, object]) -> str:
    return str(snak.get("value_type") or snak.get("type") or "string").strip()


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
        walk_snaks(stmt.qualifiers)
        walk_snaks(stmt.references)
    return pids, qids


def snak_compatible_with_test(
    *,
    property_id: str,
    value_type: str,
    value: object = None,
    property_datatypes: Mapping[str, str | None],
    existing_item_ids: set[str],
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
        if qid and qid not in existing_item_ids:
            return f"{pid} target {qid} missing on test"
    return None


def filter_item_for_test_wiki(
    item: WikidataItem,
    *,
    property_datatypes: Mapping[str, str | None],
    existing_item_ids: set[str],
) -> tuple[WikidataItem, list[str]]:
    """Drop claims/snaks that test.wikidata.org cannot accept.

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
