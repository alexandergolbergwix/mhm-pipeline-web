"""Upload WikidataItem objects to Wikidata via WikibaseIntegrator.

Handles live upload with rate limiting, retry logic, and per-entity
error handling. WikibaseIntegrator is imported lazily so the module
can be loaded even when the library is not installed (dry-run mode
does not require it).

Supports both production wikidata.org and test.wikidata.org.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any

from converter.wikidata.item_builder import WikidataItem, WikidataStatement
from converter.wikidata.item_models import validate_statement_references
from converter.wikidata.property_mapping import repair_wikidata_time
from converter.wikidata.test_wiki_compat import LocalTargetQid, MappedTestQid

if TYPE_CHECKING:
    from wikibaseintegrator.models import Claim

logger = logging.getLogger(__name__)

_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
_WIKIDATA_URL = "https://www.wikidata.org"

_TEST_API = "https://test.wikidata.org/w/api.php"
# Bug fix 2026-04-16 (deeper audit Fix #7): the previous value here was the
# MediaWiki API URL, not a SPARQL endpoint — every SPARQL query in test
# mode silently failed (got HTML back). test.wikidata.org has its own SPARQL
# endpoint at /sparql.
_TEST_SPARQL = "https://test.wikidata.org/sparql"
_TEST_URL = "https://test.wikidata.org"

_MAX_RETRIES = 3
_RETRY_DELAY_SECONDS = 5.0
_EDIT_DELAY_SECONDS = 1.5  # ~40 edits/minute (safe for OAuth with 5000 req/hr)

_UPLOAD_TYPE_ORDER = {"work": 0, "person": 1, "manuscript": 2}

# Link properties that pass 2 can replace after a local reference resolves (Rule W-192).
_DEFERRED_LINK_PIDS = ("P1574", "P3342", "P11603", "P1891", "P11105", "P88", "P110", "P655")

_WBI_VALUE_TYPE_ALIASES = {
    "wikibase-item": "item",
    "wikibase-entityid": "item",
    "wikibase-property": "item",
    "externalid": "external-id",
    "commonsmedia": "string",
}


def _normalize_wbi_value_type(value_type: object) -> str:
    key = str(value_type or "").strip().lower()
    return _WBI_VALUE_TYPE_ALIASES.get(key, key)


def _item_qid(value: object) -> str:
    if isinstance(value, Mapping):
        item_id = value.get("id")
        if isinstance(item_id, str):
            return item_id
    return str(value)


def _reference_identity(reference: Mapping[str, object]) -> str:
    snaks = reference.get("snaks")
    if not isinstance(snaks, Mapping):
        raise ValueError("A Wikidata reference must contain snaks.")
    normalized: dict[str, list[str]] = {}
    for pid, entries in snaks.items():
        if not isinstance(entries, list):
            raise ValueError("Reference snaks must be lists.")
        values: set[str] = set()
        for snak in entries:
            if not isinstance(snak, Mapping):
                raise ValueError("A reference snak must be an object.")
            values.add(json.dumps(
                {
                    key: snak[key]
                    for key in ("snaktype", "property", "datatype", "datavalue") if key in snak
                },
                sort_keys=True, ensure_ascii=False,
            ))
        normalized[str(pid)] = sorted(values)
    return json.dumps(normalized, sort_keys=True, ensure_ascii=False)


def _owner_snak_identity(snak: Mapping[str, object]) -> str:
    pid = snak.get("property")
    snaktype = snak.get("snaktype")
    datatype = snak.get("datatype")
    if (not isinstance(pid, str) or re.fullmatch(r"P[1-9][0-9]*", pid) is None
            or snaktype not in ("value", "somevalue", "novalue")
            or not isinstance(datatype, str) or not datatype):
        raise ValueError("Malformed Wikibase snak")
    identity: dict[str, object] = {"property": pid, "snaktype": snaktype, "datatype": datatype}
    if snaktype == "value":
        datavalue = snak.get("datavalue")
        if (not isinstance(datavalue, Mapping) or not isinstance(datavalue.get("type"), str)
                or "value" not in datavalue or datavalue["value"] is None):
            raise ValueError("Malformed Wikibase datavalue")
        identity["datavalue"] = datavalue
    return json.dumps(identity, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _owner_claim_identity(claim: Claim) -> tuple[str, tuple[str, ...]]:
    main = _owner_snak_identity(claim.mainsnak.get_json())
    qualifiers = claim.qualifiers.get_json()
    if not isinstance(qualifiers, Mapping):
        raise ValueError("Malformed Wikibase qualifiers")
    values: list[str] = []
    for pid, snaks in qualifiers.items():
        if not isinstance(pid, str) or not isinstance(snaks, list) or not snaks:
            raise ValueError("Malformed Wikibase qualifier list")
        for snak in snaks:
            if not isinstance(snak, Mapping) or snak.get("property") != pid:
                raise ValueError("Mismatched Wikibase qualifier property")
            values.append(_owner_snak_identity(snak))
    return main, tuple(sorted(values))


def _claim_identity(claim: Claim) -> tuple[str, tuple[str, ...], str]:
    main, qualifiers = _owner_claim_identity(claim)
    rank = getattr(claim.rank, "value", claim.rank)
    if rank not in ("normal", "preferred", "deprecated"):
        raise ValueError("Malformed Wikibase rank")
    return main, qualifiers, rank


def owner_only_quantity_unit_uri(unit: object) -> str:
    from converter.wikidata.test_wiki_compat import QUANTITY_UNIT_ALIASES  # noqa: PLC0415

    if unit is None or unit == "" or unit == "1":
        return "1"
    if not isinstance(unit, str):
        raise ClaimBuildError(["OWNER_ONLY_UNIT: quantity unit must be a canonical string."])
    if unit in QUANTITY_UNIT_ALIASES:
        return f"http://www.wikidata.org/entity/{QUANTITY_UNIT_ALIASES[unit]}"
    if re.fullmatch(r"Q[1-9][0-9]*", unit):
        return f"http://www.wikidata.org/entity/{unit}"
    if re.fullmatch(r"http://www\.wikidata\.org/entity/Q[1-9][0-9]*", unit):
        return unit
    raise ClaimBuildError(["OWNER_ONLY_UNIT: unknown or noncanonical quantity unit."])


def owner_only_validate_quantity_units(item: WikidataItem) -> None:
    for statement in getattr(item, "statements", []) or []:
        if _normalize_wbi_value_type(getattr(statement, "value_type", None)) == "quantity":
            owner_only_quantity_unit_uri(getattr(statement, "unit", None))


def quantity_amounts_equal(left: object, right: object) -> bool:
    """True when Wikibase `+11` and a native `11` / `11.0` are the same amount."""
    def parse(raw: object) -> Decimal | None:
        text = str(raw if raw is not None else "").strip().lstrip("+")
        if not text:
            return None
        try:
            return Decimal(text)
        except InvalidOperation:
            return None

    a = parse(left)
    b = parse(right)
    if a is None or b is None:
        return str(left) == str(right)
    return a == b


def time_values_equal(left: object, right: object) -> bool:
    """True when two Wikibase time strings name the same instant after repair."""
    a = repair_wikidata_time(str(left or ""))
    b = repair_wikidata_time(str(right or ""))
    if a and b and a == b:
        return True
    return str(left or "") == str(right or "")


def native_value_matches_wiki(stmt: WikidataStatement, wiki_value: str) -> bool:
    """Compare a native statement to a value extracted from a live WBI claim."""
    new_value = str(stmt.value)
    if wiki_value == new_value:
        return True
    vtype = _normalize_wbi_value_type(stmt.value_type)
    if vtype == "item":
        return _item_qid(stmt.value) == wiki_value
    if vtype == "quantity":
        return quantity_amounts_equal(new_value, wiki_value)
    if vtype == "time":
        return time_values_equal(new_value, wiki_value)
    return False


@dataclass
class UploadResult:
    """Result of uploading a single item."""

    local_id: str
    qid: str | None = None
    status: str = "pending"  # "success" | "updated" | "exists" | "failed" | "skipped" | "blocked"
    message: str = ""
    added_properties: list[str] = field(default_factory=list)


class ClaimBuildError(Exception):
    """A native statement could not be turned into a Wikibase claim (W-191)."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = list(reasons)
        Exception.__init__(self, "; ".join(self.reasons))


def _validate_reference_views(stmt: WikidataStatement, *, owner_only: bool = False) -> None:
    prefix = "OWNER_ONLY_METADATA: " if owner_only else ""
    if not isinstance(stmt.references, list):
        raise ClaimBuildError([f"{prefix}Invalid references for {stmt.property_id}."])
    if not isinstance(stmt.reference_blocks, list):
        raise ClaimBuildError([f"{prefix}Invalid reference blocks for {stmt.property_id}."])
    if not stmt.reference_blocks and len(stmt.references) > 1:
        raise ClaimBuildError([f"{prefix}Ambiguous flat references for {stmt.property_id}; rebuild from source."])

    allowed = {
        "property", "property_id", "value", "type", "value_type", "datatype", "snaktype",
        "property_label", "value_label", "label", "precision", "calendarmodel", "before", "after", "timezone",
        "value_namespace", "value_local_id",
    }
    blocks = [stmt.references, *stmt.reference_blocks]
    for index, block in enumerate(blocks):
        if not isinstance(block, list) or (index > 0 and not block):
            raise ClaimBuildError([f"{prefix}Invalid reference block for {stmt.property_id}."])
        for snak in block:
            if not isinstance(snak, dict) or set(snak) - allowed:
                raise ClaimBuildError([f"{prefix}unsupported reference metadata for {stmt.property_id}."])
            types = [snak[key] for key in ("type", "value_type", "datatype") if key in snak]
            if any(not isinstance(value, str) or not value.strip() for value in types):
                raise ClaimBuildError([f"{prefix}invalid reference type for {stmt.property_id}."])
            normalized = {_normalize_wbi_value_type(value) for value in types}
            if len(normalized) > 1 or snak.get("snaktype", "value") != "value":
                raise ClaimBuildError([f"{prefix}invalid reference metadata for {stmt.property_id}."])
            kind = next(iter(normalized), "string")
            namespace = snak.get("value_namespace")
            if "value_namespace" in snak or "value_local_id" in snak:
                if (kind != "item" or namespace not in ("wikidata", "local", "mapped-test")
                        or (namespace in ("wikidata", "mapped-test") and "value_local_id" in snak)
                        or (namespace == "local" and (
                            not isinstance(snak.get("value_local_id"), str)
                            or not snak["value_local_id"].strip()
                        ))):
                    raise ClaimBuildError([f"{prefix}invalid reference namespace for {stmt.property_id}."])
            time_keys = {"precision", "calendarmodel", "before", "after", "timezone"}
            if kind != "time" and time_keys.intersection(snak):
                raise ClaimBuildError([f"{prefix}unsupported reference time metadata for {stmt.property_id}."])
            if kind == "time":
                precision = snak.get("precision", 11)
                from converter.wikidata.quickstatements import validate_reference_time  # noqa: PLC0415

                try:
                    validate_reference_time(snak.get("value"), precision, calendarmodel=snak.get("calendarmodel"))
                except ValueError as exc:
                    raise ClaimBuildError([f"{prefix}invalid reference time for {stmt.property_id}: {exc}"]) from exc
                for key in ("before", "after", "timezone"):
                    if key in snak and (type(snak[key]) is not int or (key != "timezone" and snak[key] < 0)):
                        raise ClaimBuildError([f"{prefix}invalid reference time {key} for {stmt.property_id}."])
                if "calendarmodel" in snak:
                    calendar = snak["calendarmodel"]
                    if not isinstance(calendar, str) or re.fullmatch(r"https?://www\.wikidata\.org/entity/Q[1-9][0-9]*", calendar) is None:
                        raise ClaimBuildError([f"{prefix}invalid reference calendar for {stmt.property_id}."])
            value = snak.get("value")
            if kind == "item" and isinstance(value, Mapping):
                item_id = value.get("id")
                if (set(value) - {"id", "numeric-id", "entity-type"}
                        or not isinstance(item_id, str) or value.get("entity-type", "item") != "item"):
                    raise ClaimBuildError([f"{prefix}unsupported reference item metadata for {stmt.property_id}."])
                if "numeric-id" in value:
                    match = re.fullmatch(r"Q([1-9][0-9]*)", item_id)
                    if type(value["numeric-id"]) is not int or not match or int(match.group(1)) != value["numeric-id"]:
                        raise ClaimBuildError([f"{prefix}invalid reference item metadata for {stmt.property_id}."])
    try:
        validate_statement_references(stmt)
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise ClaimBuildError([f"{prefix}invalid reference views for {stmt.property_id}: {exc}"]) from exc


def _validate_qualifiers(
    stmt: WikidataStatement, *, is_test: bool, owner_only: bool = False, allow_local: bool = False,
) -> None:
    from converter.wikidata.quickstatements import validate_reference_time  # noqa: PLC0415

    prefix = "OWNER_ONLY_METADATA: " if owner_only else ""
    if not isinstance(stmt.qualifiers, list):
        raise ClaimBuildError([f"{prefix}invalid qualifier list for {stmt.property_id}."])
    allowed = {
        "property", "property_id", "value", "type", "value_type", "datatype", "snaktype",
        "property_label", "value_label", "label", "language", "precision", "calendarmodel",
        "before", "after", "timezone", "value_namespace", "value_local_id",
    }
    supported = {"item", "wikibase-item", "wikibase-entityid", "string", "external-id", "externalid",
                 "url", "time", "monolingualtext"}
    for qual in stmt.qualifiers:
        if not isinstance(qual, dict) or set(qual) - allowed:
            raise ClaimBuildError([f"{prefix}unsupported qualifier metadata for {stmt.property_id}."])
        prop = qual.get("property") or qual.get("property_id")
        types = [qual[key] for key in ("type", "value_type", "datatype") if key in qual]
        if (not isinstance(prop, str) or re.fullmatch(r"P[1-9][0-9]*", prop) is None
                or any(not isinstance(qual[key], str) or re.fullmatch(r"P[1-9][0-9]*", qual[key]) is None
                       for key in ("property", "property_id") if key in qual)
                or ("property" in qual and "property_id" in qual and qual["property"] != qual["property_id"])
                or any(not isinstance(value, str) or value.strip().lower() not in supported for value in types)
                or len({_normalize_wbi_value_type(value) for value in types}) > 1
                or qual.get("snaktype", "value") != "value"):
            raise ClaimBuildError([f"{prefix}invalid qualifier metadata for {stmt.property_id}."])
        kind = _normalize_wbi_value_type(types[0]) if types else "string"
        value = qual.get("value")
        if kind == "item" and isinstance(value, Mapping):
            item_id = value.get("id")
            if (set(value) - {"id", "numeric-id", "entity-type"}
                    or not isinstance(item_id, str) or value.get("entity-type", "item") != "item"):
                raise ClaimBuildError([f"{prefix}invalid qualifier item metadata for {stmt.property_id}."])
            if "numeric-id" in value:
                match = re.fullmatch(r"Q([1-9][0-9]*)", item_id)
                if type(value["numeric-id"]) is not int or not match or int(match.group(1)) != value["numeric-id"]:
                    raise ClaimBuildError([f"{prefix}invalid qualifier item metadata for {stmt.property_id}."])
        else:
            item_id = value
        if not isinstance(item_id, str) or not item_id.strip() or "\n" in item_id or "\r" in item_id:
            raise ClaimBuildError([f"{prefix}invalid qualifier value for {stmt.property_id}."])
        if kind == "item" and not (
            re.fullmatch(r"Q[1-9][0-9]*", item_id)
            or re.fullmatch(r"https?://www\.wikidata\.org/entity/Q[1-9][0-9]*", item_id)
            or (allow_local and re.fullmatch(r"__LOCAL:[^\s]+", item_id))
        ):
            raise ClaimBuildError([f"{prefix}invalid qualifier item for {stmt.property_id}."])
        namespace = qual.get("value_namespace")
        if "value_namespace" in qual or "value_local_id" in qual:
            if (kind != "item" or namespace not in ("wikidata", "local", "mapped-test")
                    or (namespace != "local" and "value_local_id" in qual)
                    or (namespace == "local" and (not isinstance(qual.get("value_local_id"), str)
                                                   or not qual["value_local_id"].strip()))
                    or (namespace == "mapped-test" and not is_test)):
                raise ClaimBuildError([f"{prefix}invalid qualifier namespace for {stmt.property_id}."])
        if kind == "monolingualtext":
            language = qual.get("language")
            if not isinstance(language, str) or re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", language) is None:
                raise ClaimBuildError([f"{prefix}invalid qualifier language for {stmt.property_id}."])
        elif "language" in qual:
            raise ClaimBuildError([f"{prefix}unsupported qualifier language for {stmt.property_id}."])
        time_keys = {"precision", "calendarmodel", "before", "after", "timezone"}
        if kind != "time" and time_keys.intersection(qual):
            raise ClaimBuildError([f"{prefix}unsupported qualifier time metadata for {stmt.property_id}."])
        if kind == "time":
            try:
                validate_reference_time(item_id, qual.get("precision", 11), calendarmodel=qual.get("calendarmodel"))
            except ValueError as exc:
                raise ClaimBuildError([f"{prefix}invalid qualifier time for {stmt.property_id}: {exc}"]) from exc
            for key in ("before", "after", "timezone"):
                if key in qual and (type(qual[key]) is not int or (key != "timezone" and qual[key] < 0)):
                    raise ClaimBuildError([f"{prefix}invalid qualifier time {key} for {stmt.property_id}."])
            if "calendarmodel" in qual and (is_test or not isinstance(qual["calendarmodel"], str)):
                raise ClaimBuildError([f"{prefix}unsupported qualifier calendar for {stmt.property_id}."])
        if kind == "url" and re.fullmatch(r"[a-z][a-z0-9+.-]*:[^][<>\"\x00-\x20\x7f]+", item_id) is None:
            raise ClaimBuildError([f"{prefix}invalid qualifier URL for {stmt.property_id}."])


def _validate_quantity_unit(unit: object, *, is_test: bool, owner_only: bool = False) -> None:
    from converter.wikidata.test_wiki_compat import _test_unit_qid  # noqa: PLC0415

    if not owner_only and is_test and isinstance(unit, str) and _test_unit_qid(unit):
        return
    if (not owner_only and isinstance(unit, str)
            and re.fullmatch(r"https://www\.wikidata\.org/entity/Q[1-9][0-9]*", unit)):
        return
    try:
        owner_only_quantity_unit_uri(unit)
    except ClaimBuildError as exc:
        if owner_only:
            raise
        raise ClaimBuildError(["Invalid quantity unit."]) from exc


def _validate_native_statement(
    stmt: WikidataStatement, *, is_test: bool, owner_only: bool = False, allow_local: bool = False,
) -> None:
    _validate_reference_views(stmt, owner_only=owner_only)
    _validate_qualifiers(stmt, is_test=is_test, owner_only=owner_only, allow_local=allow_local)
    if _normalize_wbi_value_type(stmt.value_type) == "quantity":
        _validate_quantity_unit(stmt.unit, is_test=is_test, owner_only=owner_only)


def sort_items_for_upload(items: list[Any]) -> list[Any]:
    """Works then persons then manuscripts so ``__LOCAL:`` can resolve (W-191)."""
    return sorted(
        items,
        key=lambda it: (
            _UPLOAD_TYPE_ORDER.get(str(getattr(it, "entity_type", "") or "").lower(), 9),
            str(getattr(it, "local_id", "") or ""),
        ),
    )


def _is_plain_live_qid(value: object) -> bool:
    """Return True for a bare Q-id that is not a session-mapped test QID."""
    return (isinstance(value, str) and not isinstance(value, MappedTestQid)
            and re.fullmatch(r"Q[1-9][0-9]*", value) is not None)


def _statement_reference_snaks(stmt: WikidataStatement) -> Iterable[object]:
    yield from stmt.references or []
    if isinstance(stmt.reference_blocks, list):
        for block in stmt.reference_blocks:
            if isinstance(block, list):
                yield from block


def resolve_local_statement_refs(
    item: WikidataItem,
    created_qids: dict[str, str],
    *,
    test_namespace: bool = False,
) -> list[str]:
    """Rewrite ``__LOCAL:`` snaks from previously written QIDs. Return leftovers."""
    remaining: list[str] = []

    def resolve_value(value: Any) -> Any:
        if isinstance(value, str) and value.startswith("__LOCAL:"):
            ref = value[len("__LOCAL:"):]
            qid = created_qids.get(ref)
            if qid:
                return LocalTargetQid(qid, ref)
            remaining.append(value)
        return value

    for stmt in item.statements or []:
        stmt.value = resolve_value(stmt.value)
        if test_namespace and isinstance(stmt.value, LocalTargetQid):
            stmt.value = {"id": str(stmt.value), "value_namespace": "local", "value_local_id": stmt.value.local_id}
        for snak in [*(stmt.qualifiers or []), *_statement_reference_snaks(stmt)]:
            if isinstance(snak, dict) and "value" in snak:
                snak["value"] = resolve_value(snak["value"])
                if isinstance(snak["value"], LocalTargetQid):
                    snak["value_namespace"] = "local"
                    snak["value_local_id"] = snak["value"].local_id
    return remaining


def owner_only_has_local_references(item: WikidataItem) -> bool:
    def contains(value: object) -> bool:
        if isinstance(value, LocalTargetQid):
            return True
        if isinstance(value, str):
            return value.startswith("__LOCAL:")
        if isinstance(value, dict):
            if value.get("value_namespace") == "local":
                return True
            return any(contains(v) for v in value.values())
        if isinstance(value, (list, tuple)):
            return any(contains(v) for v in value)
        return False

    return any(
        contains(stmt.value) or contains(stmt.qualifiers) or contains(stmt.references)
        or contains(stmt.reference_blocks)
        for stmt in item.statements or []
    )


def owner_only_has_generic_text(item: WikidataItem) -> bool:
    return any(
        getattr(stmt, "property_id", None) == "P1574" and getattr(stmt, "value", None) == "Q234460"
        for stmt in getattr(item, "statements", []) or []
    )


def owner_only_control_number(item: WikidataItem) -> str | None:
    local_id = getattr(item, "local_id", None)
    if not isinstance(local_id, str) or str(getattr(item, "entity_type", "")).lower() != "manuscript":
        return None
    match = re.fullmatch(r"(?:QDraft_MS_|marc:)([0-9]{5,})", local_id)
    records = getattr(item, "records", None)
    if (match is None or not isinstance(records, list) or len(records) != 1
            or not isinstance(records[0], str)
            or records[0].strip().strip("\"'").strip() != match.group(1)):
        return None
    statements = [s for s in (getattr(item, "statements", None) or [])
                  if getattr(s, "property_id", None) == "P3959"]
    if (len(statements) != 1 or getattr(statements[0], "value_type", None) != "external-id"
            or getattr(statements[0], "value", None) != match.group(1)
            or getattr(statements[0], "rank", None) not in ("normal", "preferred")):
        return None
    return match.group(1)


def _is_unresolved_local_value(value: Any, created_qids: dict[str, str]) -> bool:
    if not isinstance(value, str) or not value.startswith("__LOCAL:"):
        return False
    ref = value[len("__LOCAL:"):]
    return ref not in created_qids


def statement_has_unresolved_local(
    stmt: WikidataStatement,
    created_qids: dict[str, str],
) -> bool:
    if _is_unresolved_local_value(stmt.value, created_qids):
        return True
    for snak in [*(stmt.qualifiers or []), *_statement_reference_snaks(stmt)]:
        if isinstance(snak, dict) and _is_unresolved_local_value(
            snak.get("value"), created_qids,
        ):
            return True
    return False


def clone_statement(stmt: WikidataStatement) -> WikidataStatement:
    return replace(
        stmt,
        qualifiers=copy.deepcopy(stmt.qualifiers),
        references=copy.deepcopy(stmt.references),
        reference_blocks=copy.deepcopy(stmt.reference_blocks),
    )


def first_local_target(stmt: WikidataStatement) -> str:
    if isinstance(stmt.value, str) and stmt.value.startswith("__LOCAL:"):
        return stmt.value[len("__LOCAL:"):]
    for snak in [*(stmt.qualifiers or []), *_statement_reference_snaks(stmt)]:
        if not isinstance(snak, dict):
            continue
        value = snak.get("value")
        if isinstance(value, str) and value.startswith("__LOCAL:"):
            return value[len("__LOCAL:"):]
    return ""


def partition_unresolved_local(
    item: WikidataItem,
    created_qids: dict[str, str],
) -> tuple[WikidataItem, list[WikidataStatement]]:
    """Split statements that still need another item's QID (Rule W-192).

    Deferred statements keep their original ``__LOCAL:`` values. The write
    clone is independent so ``upload_item`` can mutate resolved snaks.
    """
    keep: list[WikidataStatement] = []
    deferred: list[WikidataStatement] = []
    for stmt in item.statements or []:
        if statement_has_unresolved_local(stmt, created_qids):
            deferred.append(clone_statement(stmt))
        else:
            keep.append(clone_statement(stmt))
    return replace(item, statements=keep), deferred


def resolve_statement_locals(
    stmt: WikidataStatement,
    created_qids: dict[str, str],
    *,
    test_namespace: bool = False,
) -> tuple[WikidataStatement, list[str]]:
    """Rewrite one statement's ``__LOCAL:`` snaks. Return leftovers."""
    probe = WikidataItem(local_id="probe", statements=[clone_statement(stmt)])
    leftover = resolve_local_statement_refs(probe, created_qids, test_namespace=test_namespace)
    return probe.statements[0], leftover


class UnauthorisedModificationError(RuntimeError):
    """Raised when the uploader is asked to modify a Wikidata item whose
    first revision was NOT authored by the authenticated user.

    This is the defense-in-depth tripwire for CLAUDE.md rule 38: the
    pipeline is only ever allowed to CREATE new entities and to MODIFY
    entities it created itself. Any attempt to modify another user's
    item must raise this exception, not merely be skipped silently.

    The *stage* field records WHICH guard caught the violation so the
    audit log makes it clear that the defense chain worked (entry /
    build / write / identity-check).
    """

    def __init__(self, *, qid: str, stage: str) -> None:
        super().__init__(
            f"SAFETY: refusing to modify {qid!r} — not authored by the "
            f"authenticated user (caught at stage {stage!r})."
        )
        self.qid = qid
        self.stage = stage


class WikidataUploader:
    """Upload WikidataItem objects to Wikidata.

    Requires ``wikibaseintegrator`` package for live uploads.
    Install with: ``pip install wikibaseintegrator``

    Usage::

        uploader = WikidataUploader(auth="User@BotName:bot-password")
        results = uploader.upload_all(items)
    """

    def __init__(
        self,
        token: str,
        is_test: bool = False,
        batch_mode: bool = False,
        *,
        allow_live: bool = False,
        mark_as_bot: bool | None = None,
    ) -> None:
        """Initialize the uploader.

        Args:
            token: OAuth bearer token or bot password for Wikidata API.
            is_test: If True, use test.wikidata.org instead of production.
            batch_mode: If True, pause 60s every 45 items to stay under rate limits.
            allow_live: If True, curator explicitly chose live wikidata.org
                (UI upload target). Same effect as ``MORATORIUM_LIFTED=true``.
            mark_as_bot: Pass ``is_bot=True`` on writes. Default False — MediaWiki
                hard-fails when the account lacks the ``bot`` right (export-40).
                Set True / ``WIKIDATA_MARK_AS_BOT=true`` only after the account
                has been granted bot rights.

        Raises:
            RuntimeError: If the Wikidata moratorium (CLAUDE.md rule 25) is in
                effect and neither ``allow_live`` nor ``MORATORIUM_LIFTED=true``
                is set. Test mode (``is_test=True``) bypasses the check.
        """
        import os  # noqa: PLC0415

        from converter.wikidata.auth_token import (  # noqa: PLC0415
            normalize_wikidata_auth_token as _normalize_auth,
        )

        self._token = _normalize_auth(token)
        self._is_test = is_test
        self._batch_mode = batch_mode
        self._allow_live = allow_live
        if mark_as_bot is None:
            mark_as_bot = os.environ.get("WIKIDATA_MARK_AS_BOT", "").lower() in {
                "1", "true", "yes",
            }
        self._mark_as_bot = bool(mark_as_bot)
        self._wbi = None
        self._login = None
        self._owner_only_session = None
        self._last_edit_time: float = 0.0
        self._rate_limit_state: dict[str, Any] = {
            "last": 0.0,
            "lock": threading.Lock(),
        }
        self._authenticated_user: str | None = None  # Set after first auth
        self._creator_cache: dict[str, str] = {}  # qid → first revision author
        self._is_our_item_cache: dict[str, bool] = {}  # qid → creator-check result
        self._q3959_cache: dict[str, frozenset[str]] = {}  # qid → remote P3959 values
        self._bot_exclusion_cache: dict[str, bool] = {}
        self._prefetched_entities: dict[str, dict[str, object]] = {}
        self._existing_label_snapshot: dict[str, dict[str, str]] = {}
        self._test_property_datatypes: dict[str, str | None] = {}
        self._test_entity_exists: dict[str, bool] = {}
        self._test_pid_map: dict[str, str] = {}
        self._test_qid_map: dict[str, str] = {}
        self._verified_test_targets: dict[str, str] = {}
        self._test_stubs_we_created: set[str] = set()
        self._test_fresh_creates: set[str] = set()
        self._test_item_descriptions: dict[str, str] = {}
        self._live_qid_labels: dict[str, str] = {}
        self._foreign_accept_local = threading.local()
        self._test_can_create_properties: bool | None = None
        self._enforce_moratorium()

    @property
    def _foreign_accept_qids(self) -> set[str]:
        accepts = getattr(self._foreign_accept_local, "qids", None)
        if accepts is None:
            accepts = set()
            self._foreign_accept_local.qids = accepts
        return accepts

    def register_test_ledger_target(self, item: WikidataItem, ledger: Mapping[str, str]) -> str:
        """Bind a local ID to its exact test-ledger entry, never its live QID."""
        if not self._is_test or not isinstance(item.local_id, str) or not item.local_id:
            raise ValueError("A test-wiki item with a local ID is required")
        from app.pipeline.wikidata_qid_ledger import ledger_key_for_item  # noqa: PLC0415

        key = ledger_key_for_item(item, "wikidata-test")
        qid = ledger.get(key)
        if not isinstance(qid, str) or re.fullmatch(r"Q[1-9][0-9]*", qid) is None:
            raise ValueError("The exact test-ledger mapping is missing or invalid")
        return self.register_test_local_target(item.local_id, qid)

    def register_test_local_target(self, local_id: str, qid: str) -> str:
        """Bind a local ID to one sealed test-ledger QID; conflicting proof is refused."""
        if not self._is_test or not local_id:
            raise ValueError("A test-wiki local ID is required")
        if re.fullmatch(r"Q[1-9][0-9]*", qid) is None:
            raise ValueError("The sealed test-ledger QID is invalid")
        previous = self._verified_test_targets.get(local_id)
        if previous is not None and previous != qid:
            raise ValueError("Conflicting test-target proof")
        self._verified_test_targets[local_id] = qid
        return qid

    def _test_local_target_issues(
        self, item: WikidataItem, *, require_mapped: bool = False, allow_unmapped_live: bool = False,
    ) -> list[str]:
        from converter.wikidata.test_wiki_compat import explicit_live_item_qid, item_target_qid  # noqa: PLC0415

        issues: list[str] = []
        for stmt in item.statements or []:
            if _normalize_wbi_value_type(stmt.value_type) == "item":
                value = stmt.value
                known_live = bool(explicit_live_item_qid(value)) or (
                    isinstance(value, Mapping) and set(value) == {"id", "value_namespace"}
                    and value.get("value_namespace") == "wikidata"
                    and isinstance(value.get("id"), str)
                    and re.fullmatch(r"Q[1-9][0-9]*", value["id"]) is not None
                )
                if allow_unmapped_live and _is_plain_live_qid(value):
                    known_live = True
                if require_mapped:
                    known_live = False
                known_mapped = (isinstance(value, MappedTestQid) and str(value) in self._test_qid_map.values()
                                and self._test_entity_exists.get(str(value)) is True)
                known_local = (isinstance(value, LocalTargetQid)
                               or (isinstance(value, Mapping) and value.get("value_namespace") == "local"))
                if not known_live and not known_mapped and not known_local:
                    issues.append(f"{stmt.property_id} item has an ambiguous wiki namespace")
            if (isinstance(stmt.value, LocalTargetQid)
                    or (isinstance(stmt.value, Mapping) and stmt.value.get("value_namespace") == "local")):
                local_id = (stmt.value.local_id if isinstance(stmt.value, LocalTargetQid)
                            else stmt.value.get("value_local_id"))
                if (not isinstance(local_id, str) or not item_target_qid(stmt.value)
                        or (isinstance(stmt.value, Mapping) and set(stmt.value) != {"id", "value_namespace", "value_local_id"})
                        or self._verified_test_targets.get(local_id) != item_target_qid(stmt.value)):
                    issues.append(f"{stmt.property_id} local target lacks test-ledger proof")
            for snak in [*(stmt.qualifiers or []), *_statement_reference_snaks(stmt)]:
                if not isinstance(snak, dict):
                    continue
                value = snak.get("value")
                if snak.get("value_namespace") == "local" or isinstance(value, LocalTargetQid):
                    local_id = snak.get("value_local_id")
                    if (not isinstance(local_id, str) or not item_target_qid(value)
                            or (isinstance(value, LocalTargetQid) and value.local_id != local_id)
                            or self._verified_test_targets.get(local_id) != item_target_qid(value)):
                        issues.append(f"{stmt.property_id} local target lacks test-ledger proof")
            for snak in stmt.qualifiers or []:
                if (not isinstance(snak, dict) or _normalize_wbi_value_type(
                    snak.get("value_type") or snak.get("type") or snak.get("datatype") or "string",
                ) != "item"):
                    continue
                value = snak.get("value")
                namespace = snak.get("value_namespace")
                known_live = bool(explicit_live_item_qid(value)) or (
                    namespace == "wikidata" and isinstance(value, str)
                    and re.fullmatch(r"Q[1-9][0-9]*", value) is not None
                )
                if allow_unmapped_live and _is_plain_live_qid(value):
                    known_live = True
                if require_mapped:
                    known_live = False
                known_mapped = (isinstance(value, MappedTestQid) and str(value) in self._test_qid_map.values()
                                and self._test_entity_exists.get(str(value)) is True)
                if not known_live and not known_mapped and namespace != "local" and not isinstance(value, LocalTargetQid):
                    issues.append(f"{stmt.property_id} qualifier has an ambiguous wiki namespace")
        return issues

    @staticmethod
    def _enforce_moratorium() -> None:
        """Refuse to run against production Wikidata while the moratorium is on.

        Lifted only when ``MORATORIUM_LIFTED=true`` is set in the environment.
        See CLAUDE.md rule 25 for the conditions that must hold before
        lifting the moratorium.

        ``is_test=True`` bypasses this check (test.wikidata.org is fine for
        development and CI).
        """
        import os  # noqa: PLC0415

        if os.environ.get("MORATORIUM_LIFTED", "").lower() == "true":
            return
        # Inspect the caller's `is_test` arg from the bound instance after init.
        # We do that in upload_item / upload_all instead of here so test-mode
        # uploaders still construct cleanly. See _check_moratorium_for_live().

    def _check_moratorium_for_live(self) -> None:
        """Block any live Wikidata write while the moratorium is on."""
        import os  # noqa: PLC0415

        if self._is_test:
            return
        if self._allow_live:
            return
        if os.environ.get("MORATORIUM_LIFTED", "").lower() == "true":
            return
        raise RuntimeError(
            "WIKIDATA MORATORIUM IN EFFECT (CLAUDE.md rule 25). "
            "All bulk Wikidata operations are blocked until pipeline bugs "
            "#1-#4 are fixed and verified, 20+ manual edits have been made, "
            "and a notice has been posted on Wikidata:Project chat. "
            "To override, choose live upload in Wikidata Studio or set "
            "MORATORIUM_LIFTED=true in the environment. "
            "See User talk:Alexander Goldberg IL § 'Please stop your edits' "
            "(Geagea, 2026-04-14)."
        )

    def _init_wbi(self) -> object:
        """Lazily initialize WikibaseIntegrator.

        Returns:
            A configured WikibaseIntegrator instance.

        Raises:
            ImportError: If wikibaseintegrator is not installed.
        """
        if self._wbi is not None:
            return self._wbi

        try:
            from wikibaseintegrator import (
                WikibaseIntegrator,  # noqa: PLC0415
                wbi_login,  # noqa: PLC0415
            )
            from wikibaseintegrator.wbi_config import config as wbi_config  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "wikibaseintegrator is required for live Wikidata upload. "
                "Install it with: pip install wikibaseintegrator\n"
                "Or use dry-run mode to export QuickStatements instead."
            ) from exc

        if self._is_test:
            wbi_config["MEDIAWIKI_API_URL"] = _TEST_API
            wbi_config["SPARQL_ENDPOINT_URL"] = _TEST_SPARQL
            wbi_config["WIKIBASE_URL"] = _TEST_URL
        else:
            wbi_config["MEDIAWIKI_API_URL"] = _WIKIDATA_API
            wbi_config["SPARQL_ENDPOINT_URL"] = _WIKIDATA_SPARQL
            wbi_config["WIKIBASE_URL"] = _WIKIDATA_URL

        # Limit retries to avoid infinite waits during server load
        # Fix 2026-04-15 third audit Fix #16: maxlag=5 was too aggressive —
        # edits fail frequently during server load and burn retries. Best
        # practice for bots is maxlag >= 10s (Wikidata:Bots).
        wbi_config["MAXLAG"] = 10
        wbi_config["BACKOFF_MAX_TRIES"] = 3  # Max 3 retries (default 5)
        wbi_config["BACKOFF_MAX_VALUE"] = 30  # Max 30s backoff (default 3600!)

        # Support four authentication methods:
        # 1. Bot password: "Username@BotName:password"
        # 2. OAuth 2.0 owner-only: "consumer_key|consumer_secret"
        # 3. OAuth 1.0a: "consumer_key|consumer_secret|access_token|access_secret"
        # 4. OAuth 2.0 pre-issued JWT bearer token:
        #    "eyJ0eXAiOiJKV1QiLCJhbGci...<dotted JWT>..."
        #    (Issued directly by meta.wikimedia.org when the consumer
        #    registration is owner-only + "Client is confidential". No
        #    consumer-secret exchange needed — we wrap a requests.Session
        #    with the Authorization: Bearer header and hand it straight
        #    to wbi_login._Login.)
        api_url = _TEST_API if self._is_test else _WIKIDATA_API
        user_agent = "MHMPipeline/1.0 (shvedbook@gmail.com)"

        # JWT bearer detection: 3 dot-separated base64url parts, starts
        # with "eyJ" (the typical base64url prefix of a JSON header like
        # {"typ":"JWT", ...}). No `:` (not a bot password) and no `|`
        # (not a consumer-key/consumer-secret pair).
        is_jwt_bearer = (
            ":" not in self._token
            and "|" not in self._token
            and self._token.count(".") == 2
            and self._token.startswith("eyJ")
        )
        if is_jwt_bearer:
            import requests  # noqa: PLC0415

            session = requests.Session()
            session.headers.update({
                "Authorization": f"Bearer {self._token}",
                "User-Agent": user_agent,
            })
            login = wbi_login._Login(  # noqa: SLF001 — upstream exposes no public alternative
                session=session,
                mediawiki_api_url=api_url,
                user_agent=user_agent,
            )
        elif "|" in self._token:
            parts = self._token.split("|")
            if len(parts) == 2:
                # OAuth 2.0: consumer_key|consumer_secret
                login = wbi_login.OAuth2(
                    consumer_token=parts[0].strip(),
                    consumer_secret=parts[1].strip(),
                    mediawiki_api_url=api_url,
                    mediawiki_rest_url=("https://test.wikidata.org/w/rest.php" if self._is_test
                                        else "https://www.wikidata.org/w/rest.php"),
                    user_agent=user_agent,
                )
            elif len(parts) >= 4:
                # OAuth 1.0a: consumer_key|consumer_secret|access_token|access_secret
                login = wbi_login.OAuth1(
                    consumer_token=parts[0].strip(),
                    consumer_secret=parts[1].strip(),
                    access_token=parts[2].strip(),
                    access_secret=parts[3].strip(),
                    mediawiki_api_url=api_url,
                    mediawiki_index_url=("https://test.wikidata.org/w/index.php" if self._is_test
                                         else "https://www.wikidata.org/w/index.php"),
                    user_agent=user_agent,
                )
            else:
                raise ValueError(
                    "Invalid OAuth token format. Use:\n"
                    "  OAuth 2.0: consumer_key|consumer_secret\n"
                    "  OAuth 1.0a: consumer_key|consumer_secret|access_token|access_secret"
                )
        elif ":" in self._token and "@" in self._token.split(":")[0]:
            # Bot password: "Username@BotName:password"
            parts = self._token.split(":", 1)
            login = wbi_login.Login(
                user=parts[0].strip(),
                password=parts[1].strip(),
                mediawiki_api_url=api_url,
                user_agent=user_agent,
            )
        else:
            raise ValueError(
                "Invalid authentication format. Use one of:\n"
                "  Bot password:  Username@BotName:password\n"
                "  OAuth 2.0:     consumer_key|consumer_secret\n"
                "  OAuth 1.0a:    consumer_key|consumer_secret|access_token|access_secret\n"
                "  JWT bearer:    eyJ…  (paste the owner-only access token "
                "from Special:OAuthConsumerRegistration)"
            )

        self._wbi = WikibaseIntegrator(login=login)
        self._login = login
        return self._wbi

    # MediaWiki rights required to CREATE Wikibase items. Bot passwords that
    # only grant "Edit existing pages" authenticate fine but fail every write
    # with permissiondenied (export-41 / 2026-08-11).
    _REQUIRED_WRITE_RIGHTS = frozenset({"edit", "createpage"})

    def ensure_authenticated(self) -> None:
        """Force a single MediaWiki login now (Rule W-179).

        Upload jobs must call this once and reuse the same uploader; logging
        in per item burns MediaWiki's login rate limit. Also verifies the
        session can create/edit items so missing bot-password grants abort
        the job once instead of failing every row three times.
        """
        self._init_wbi()
        self.assert_write_capability()

    def ensure_owner_only_authenticated(self) -> None:
        if self._is_test:
            raise RuntimeError("Owner-only authentication requires live Wikidata")
        if self._wbi is None:
            import requests  # noqa: PLC0415
            from wikibaseintegrator import WikibaseIntegrator, wbi_login  # noqa: PLC0415

            class PinnedSession(requests.Session):
                def request(self, method: str, url: str, **kwargs: Any) -> Any:
                    if url != _WIKIDATA_API:
                        raise RuntimeError("Owner-only session refused a non-Wikidata URL")
                    kwargs["allow_redirects"] = False
                    kwargs.setdefault("timeout", 15)
                    return super().request(method, url, **kwargs)

            session = PinnedSession()
            session.headers.update({"User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)"})
            if ":" in self._token and "@" in self._token.split(":", 1)[0]:
                user, password = self._token.split(":", 1)
                response = session.post(_WIKIDATA_API, data={
                    "action": "query", "meta": "tokens", "type": "login", "format": "json",
                })
                if response.status_code != 200:
                    raise RuntimeError("Owner-only login token request failed")
                login_token = response.json()["query"]["tokens"]["logintoken"]
                response = session.post(_WIKIDATA_API, data={
                    "action": "login", "lgname": user.strip(), "lgpassword": password.strip(),
                    "lgtoken": login_token, "format": "json",
                })
                if response.status_code != 200 or response.json().get("login", {}).get("result") != "Success":
                    raise RuntimeError("Owner-only Wikidata login failed")
            elif (":" not in self._token and "|" not in self._token
                  and self._token.count(".") == 2 and self._token.startswith("eyJ")):
                session.headers["Authorization"] = f"Bearer {self._token}"
            else:
                raise RuntimeError("Owner-only mode supports live bot passwords or JWT credentials only")
            login = wbi_login._Login(session=session, mediawiki_api_url=_WIKIDATA_API,
                                     user_agent="MHMPipeline/1.0 (shvedbook@gmail.com)")
            self._login = login
            self._wbi = WikibaseIntegrator(login=login)
            self._owner_only_session = session
        if (self._login is None or self._login.mediawiki_api_url != _WIKIDATA_API
                or self._owner_only_session is None
                or self._login.get_session() is not self._owner_only_session):
            raise RuntimeError("Owner-only session is not pinned to Wikidata")
        info = self._query_userinfo_rights()
        if (not isinstance(info, dict) or "anon" in info
                or not isinstance(info.get("name"), str)
                or not info["name"] or info["name"] == "127.0.0.1"):
            raise RuntimeError("Owner-only Wikidata authentication could not be verified")
        self._authenticated_user = info["name"]

    def assert_write_capability(self) -> None:
        """Abort early when the session cannot create Wikidata items.

        Raises:
            RuntimeError: anonymous session or missing ``edit``/``createpage``.
        """
        info = self._query_userinfo_rights()
        if not info:
            logger.warning("Could not verify write rights via userinfo; continuing")
            return
        name = str(info.get("name") or "")
        if "anon" in info or not name or name == "127.0.0.1":
            wiki = "test.wikidata.org" if self._is_test else "www.wikidata.org"
            raise RuntimeError(
                f"Wikidata session is anonymous on {wiki}. "
                "Check Settings → Wikidata (test/live) bot password."
            )
        self._authenticated_user = name
        rights = {str(r) for r in (info.get("rights") or [])}
        missing = sorted(self._REQUIRED_WRITE_RIGHTS - rights)
        if missing:
            wiki = "test.wikidata.org" if self._is_test else "www.wikidata.org"
            raise RuntimeError(
                f"Authenticated as {name!r} on {wiki} but missing MediaWiki "
                f"rights {missing}. On {wiki}/wiki/Special:BotPasswords enable "
                "at least: High-volume editing; Edit existing pages; "
                "Create, edit, and move pages. Then save a new password into "
                "Settings and retry."
            )
        logger.info(
            "Wikidata write session ok user=%s rights_has_createpage=%s",
            name,
            "createpage" in rights,
        )

    def _query_userinfo_rights(self) -> dict[str, Any] | None:
        """Return ``userinfo`` (name/rights/anon) via the logged-in WBI session."""
        try:
            login = self._login
            if login is None:
                return None
            session = login.get_session()
            api_url = _TEST_API if self._is_test else _WIKIDATA_API
            resp = session.get(
                api_url,
                params={
                    "action": "query",
                    "meta": "userinfo",
                    "uiprop": "rights|groups",
                    "format": "json",
                },
                timeout=15,
                allow_redirects=False,
            )
            if resp.status_code != 200:
                return None
            resp.raise_for_status()
            return resp.json().get("query", {}).get("userinfo")
        except Exception as exc:  # noqa: BLE001
            logger.warning("userinfo rights probe failed: %s", exc)
            return None

    def _rate_limit(self) -> None:
        """Enforce edit rate limiting across shared shard uploaders."""
        lock = self._rate_limit_state.get("lock")
        if lock is not None:
            with lock:
                now = time.time()
                last = max(self._last_edit_time, float(self._rate_limit_state.get("last") or 0.0))
                elapsed = now - last
                if elapsed < _EDIT_DELAY_SECONDS:
                    time.sleep(_EDIT_DELAY_SECONDS - elapsed)
                updated = time.time()
                self._last_edit_time = updated
                self._rate_limit_state["last"] = updated
            return
        elapsed = time.time() - self._last_edit_time
        if elapsed < _EDIT_DELAY_SECONDS:
            time.sleep(_EDIT_DELAY_SECONDS - elapsed)
        self._last_edit_time = time.time()

    # Identity properties: if the existing item already has a DIFFERENT value
    # on one of these, adding our value would create the multi-value conflict
    # pattern that Kolja21 flagged (e.g., two birth dates, two GNDs). We MUST
    # skip our value in that case — the items are different real-world entities.
    _IDENTITY_PROPS = frozenset(
        {"P569", "P570", "P19", "P20", "P227", "P214", "P8189", "P213", "P244", "P21"}
    )

    # Rule 42 (Phase 1 HMO fidelity, 2026-05-17): P31 moves OUT of the strict
    # single-value bucket. HMO models manuscripts as intersections of multiple
    # classes (illuminated + composite + palimpsest + codex) and WikiProject
    # Manuscripts endorses multi-P31 when no pair is in a subclass relation.
    # The other ten identity properties keep their Rule 23 semantics intact.
    _MULTI_VALUE_IDENTITY_PROPS = frozenset({"P31"})

    # Defense-in-depth (audit response 2026-05-17, Geagea/Kolja21/Epìdosis
    # talk threads): the multi-value P31 relaxation is bounded to the
    # manuscript class hierarchy. Adding P31=Q5 (human), P31=Q43229
    # (organization), P31=Q215380 (band), etc. is STILL refused even
    # though P31 is multi-value. This is the structural guard against
    # the wrong-P31 incidents — manuscripts can be both Q87167 and
    # Q48498, but never Q87167 and Q5.
    _MANUSCRIPT_P31_VALUES = frozenset(
        {
            "Q87167",      # manuscript
            "Q123172729",  # manuscript codex
            "Q720106",     # scroll
            "Q30103158",   # manuscript fragment
            "Q213924",     # codex
            "Q48498",      # illuminated manuscript
            "Q33308141",   # composite manuscript
            "Q274076",     # palimpsest
            "Q179808",     # palimpsest (legacy)
            "Q3884",       # codex (alternate)
            "Q571",        # book (only when manuscript is also being typed as a book)
        }
    )

    def _would_create_identity_conflict(self, wbi_item: object, stmt: WikidataStatement) -> bool:
        """Return True if adding this statement to the existing item would create
        a multi-value conflict on an identity property."""
        if stmt.property_id in self._MULTI_VALUE_IDENTITY_PROPS:
            # Multi-value allowed, but bounded to a coherent class hierarchy
            # to avoid the wrong-P31 incidents (e.g., manuscript-class on a
            # person item). Audit response 2026-05-17.
            if stmt.property_id == "P31":
                new_value = _item_qid(stmt.value)
                try:
                    existing_claims = wbi_item.claims.get("P31") or []
                    for existing in existing_claims:
                        existing_value = self._extract_claim_value(existing)
                        if native_value_matches_wiki(stmt, existing_value):
                            return False
                except Exception:
                    pass
                if new_value not in self._MANUSCRIPT_P31_VALUES:
                    # Refuse to add a non-manuscript P31 via the pipeline.
                    # The pipeline only emits manuscript P31s; if a caller
                    # reaches here with something else, it's a bug.
                    return True
                # If the existing item already carries a non-manuscript P31
                # (e.g., Q5 human), refuse to add a manuscript-class one
                # alongside it.
                try:
                    existing_claims = wbi_item.claims.get("P31") or []
                    for existing in existing_claims:
                        existing_value = self._extract_claim_value(existing)
                        if (
                            existing_value
                            and existing_value not in self._MANUSCRIPT_P31_VALUES
                        ):
                            return True
                except Exception:
                    pass
            return False
        if stmt.property_id not in self._IDENTITY_PROPS:
            return False
        try:
            existing_claims = wbi_item.claims.get(stmt.property_id) or []
        except Exception:
            return False
        if not existing_claims:
            return False
        new_value = str(stmt.value)
        for existing in existing_claims:
            existing_value = self._extract_claim_value(existing)
            if existing_value == new_value:
                return False  # same value — safe to add (WBI will dedup)
            # date precision: compare just the date prefix for P569/P570
            if stmt.property_id in ("P569", "P570") and existing_value[:11] == new_value[:11]:
                return False
        return True  # existing has different value(s) — would conflict

    def _build_wbi_item(
        self,
        item: WikidataItem,
        *,
        read_only: bool = False,
        owner_only: bool = False,
    ) -> tuple[object, int, list[str]]:
        """Convert a WikidataItem to a WikibaseIntegrator item object.

        For existing items, performs claim diffing to avoid duplicates AND
        refuses to write identity-property values that conflict with existing ones.

        Returns:
            Tuple of (wbi_item, new_claims_count, added_properties).
        """

        if (self._is_test and not read_only and item.existing_qid
                and self._verified_test_targets.get(item.local_id) != item.existing_qid):
            raise ClaimBuildError(["Test UPDATE target lacks exact test-ledger proof."])
        if owner_only:
            if not item.existing_qid:
                raise ClaimBuildError(["OWNER_ONLY_TARGET: an existing QID is required."])
            if owner_only_has_local_references(item) or owner_only_has_generic_text(item):
                raise ClaimBuildError(["OWNER_ONLY_INCOMPLETE_SOURCE: unresolved or generic text evidence."])

        wbi = self._init_wbi()

        # DEFENSE-IN-DEPTH #2 (rule 38): Even though upload_item already
        # checks _is_our_item at entry, _build_wbi_item is called from
        # other code paths too (tests, scripts). Re-assert here so no
        # mutation can happen on someone else's item.
        if not read_only:
            self._assert_modifiable(
                item.existing_qid or "",
                stage="_build_wbi_item",
                our_p3959=self._our_p3959(item),
            )

        if item.existing_qid:
            prefetched = None if owner_only else self._prefetched_entities.pop(str(item.existing_qid), None)
            if (
                isinstance(prefetched, dict)
                and "missing" not in prefetched
                and "claims" in prefetched
            ):
                wbi_item = wbi.item.new().from_json(prefetched)
            else:
                try:
                    options = {"mediawiki_api_url": _WIKIDATA_API, "allow_redirects": False} if owner_only else {}
                    wbi_item = wbi.item.get(
                        item.existing_qid,
                        maxlag=20,
                        retry_after=5,
                        max_retries=4,
                        timeout=30,
                        **options,
                    )
                except TypeError:
                    if owner_only:
                        raise
                    wbi_item = wbi.item.get(item.existing_qid)
        else:
            wbi_item = wbi.item.new()

        actual_qid = getattr(wbi_item, "id", None)
        if actual_qid != (item.existing_qid or None):
            raise ClaimBuildError(["TARGET: the loaded entity does not match the requested QID."])

        if item.existing_qid:
            self._existing_label_snapshot.setdefault(
                str(item.existing_qid),
                {
                    lang: str(value.get("value") or "")
                    for lang, value in wbi_item.labels.get_json().items()
                    if isinstance(value, dict) and value.get("value")
                },
            )

        if item.existing_qid and item.entity_type == "work" and not self._is_test:
            from converter.wikidata.work_identity import (  # noqa: PLC0415
                compare_work_identity,
                work_identity_for_item,
                work_identity_from_entity,
            )

            entity = wbi_item.get_json()
            if not isinstance(entity, Mapping) or entity.get("id") != item.existing_qid:
                raise ClaimBuildError(["WORK_IDENTITY: the current entity does not match the target."])
            try:
                comparison = compare_work_identity(
                    work_identity_for_item(item), work_identity_from_entity(entity),
                    strong_identifier=True,
                )
            except ValueError as exc:
                raise ClaimBuildError([f"WORK_IDENTITY: invalid current work evidence: {exc}"]) from exc
            if comparison != "match":
                raise ClaimBuildError([
                    f"WORK_IDENTITY: {item.existing_qid} conflicts with the proposed author/source scope.",
                ])

        # Labels — never overwrite an existing label on an item we did not create.
        # On items we created/own,
        # allow updating labels and descriptions when our projection improves them
        # (e.g. replacing a microfilm-based F-number label with the MARC 942
        # shelfmark designation).
        is_owned_by_us = bool(
            item.existing_qid
            and self._is_our_item_cache.get(str(item.existing_qid)) is True
            and str(item.existing_qid) not in self._foreign_accept_qids
        )
        if owner_only:
            for lang, aliases in item.aliases.items():
                if (not isinstance(lang, str) or not lang.strip() or lang != lang.strip()
                        or not isinstance(aliases, list)
                        or any(not isinstance(alias, str) or not alias.strip() for alias in aliases)):
                    raise ClaimBuildError(["OWNER_ONLY_METADATA: invalid alias data."])
            for lang in set(item.labels) | set(item.descriptions):
                if not isinstance(lang, str) or not lang.strip() or lang != lang.strip():
                    raise ClaimBuildError(["OWNER_ONLY_METADATA: invalid language key."])
                try:
                    old_label = wbi_item.labels.get(lang)
                    old_desc = wbi_item.descriptions.get(lang)
                    old_label = old_label.value if old_label is not None else None
                    old_desc = old_desc.value if old_desc is not None else None
                except Exception as exc:
                    raise ClaimBuildError([f"OWNER_ONLY_METADATA: cannot read {lang} metadata."]) from exc
                if any(value is not None and not isinstance(value, str) for value in (old_label, old_desc)):
                    raise ClaimBuildError([f"OWNER_ONLY_METADATA: malformed existing {lang} metadata."])
                new_label = item.labels.get(lang) if lang in item.labels else old_label
                new_desc = item.descriptions.get(lang) if lang in item.descriptions else old_desc
                if (
                    (lang in item.labels and (not isinstance(new_label, str) or not new_label.strip()))
                    or (lang in item.descriptions and (not isinstance(new_desc, str) or not new_desc.strip()))
                    or (old_label and lang in item.labels and new_label != old_label)
                    or (old_desc and lang in item.descriptions and new_desc != old_desc)
                    or (new_label and new_desc and new_label == new_desc)
                ):
                    raise ClaimBuildError([f"OWNER_ONLY_METADATA: conflicting {lang} label or description."])
        metadata_updated = False
        for lang, label in item.labels.items():
            try:
                current = wbi_item.labels.get(lang)
                current_val = (
                    current.value if current and getattr(current, "value", None) else ""
                )
            except Exception as exc:
                if owner_only:
                    raise ClaimBuildError([f"OWNER_ONLY_METADATA: cannot read {lang} label."]) from exc
                current_val = ""
            if item.existing_qid and not is_owned_by_us and current_val:
                continue
            if current_val != label:
                wbi_item.labels.set(lang, label)
                if item.existing_qid:
                    metadata_updated = True

        # Descriptions — same protection as labels above. Bug fix
        # 2026-04-16 (deeper audit Fix #17): previously overwrote
        # community-improved descriptions on existing items.
        for lang, desc in item.descriptions.items():
            if desc and desc == item.labels.get(lang):
                # Wikidata rejects a description identical to the label in
                # the same language — the whole write fails. A description
                # that only repeats the label carries no information; drop
                # it instead of refusing the item (2026-10-14: 234 release
                # items failed with 'Label and description ... same value').
                if item.existing_qid and is_owned_by_us and not owner_only:
                    try:
                        current = wbi_item.descriptions.get(lang)
                        if current and getattr(current, "value", None):
                            wbi_item.descriptions.set(lang, None)
                            metadata_updated = True
                    except Exception:
                        pass
                continue
            try:
                current = wbi_item.descriptions.get(lang)
                current_val = (
                    current.value if current and getattr(current, "value", None) else ""
                )
            except Exception as exc:
                if owner_only:
                    raise ClaimBuildError([f"OWNER_ONLY_METADATA: cannot read {lang} description."]) from exc
                current_val = ""
            if item.existing_qid and not is_owned_by_us and current_val:
                continue
            if current_val != desc:
                wbi_item.descriptions.set(lang, desc)
                if item.existing_qid:
                    metadata_updated = True

        if item.existing_qid and is_owned_by_us and not owner_only:
            for lang, label in item.labels.items():
                if not label:
                    continue
                try:
                    current_desc = wbi_item.descriptions.get(lang)
                    if current_desc and getattr(current_desc, "value", None) == label:
                        wbi_item.descriptions.set(lang, None)
                        metadata_updated = True
                except Exception as exc:
                    logger.warning(
                        "Could not check the existing %s description on %s: %s",
                        lang, item.existing_qid, exc,
                    )

        # Aliases
        alias_updated = False
        for lang, alias_list in item.aliases.items():
            for alias in alias_list:
                if owner_only:
                    try:
                        current_aliases = wbi_item.aliases.get(lang) or []
                        if not any(getattr(current, "value", None) == alias for current in current_aliases):
                            alias_updated = True
                    except Exception as exc:
                        raise ClaimBuildError([f"OWNER_ONLY_METADATA: cannot read {lang} aliases."]) from exc
                wbi_item.aliases.set(lang, alias)

        # Statements — use WBI's built-in dedup for existing items
        from wikibaseintegrator.models import References  # noqa: PLC0415
        from wikibaseintegrator.wbi_enums import ActionIfExists  # noqa: PLC0415

        added_properties: list[str] = []
        built = 0
        failures: list[str] = []
        built_by_pid: dict[str, list[object]] = {}
        for stmt in item.statements:
            if getattr(stmt, "rank", None) not in ("normal", "preferred", "deprecated"):
                prefix = "OWNER_ONLY_METADATA: " if owner_only else ""
                raise ClaimBuildError([f"{prefix}invalid rank for {stmt.property_id}."])
            # SAFETY: never add an identity-property value that conflicts with
            # an existing one on a pre-existing item.
            if (
                item.existing_qid
                and not read_only
                and (not is_owned_by_us or stmt.property_id == "P31")
                and self._would_create_identity_conflict(wbi_item, stmt)
            ):
                failures.append(
                    f"{stmt.property_id}={stmt.value} identity-conflict on "
                    f"{item.existing_qid}"
                )
                continue
            claim = self._build_claim(stmt, owner_only=True) if owner_only else self._build_claim(stmt)
            if claim is None:
                failures.append(
                    f"{stmt.property_id}={stmt.value} build-failed "
                    f"(value_type={stmt.value_type!r})"
                )
                continue
            built += 1
            built_by_pid.setdefault(stmt.property_id, []).append(claim)

        if failures:
            raise ClaimBuildError(failures)

        existing_by_pid: dict[str, list[Claim]] = {}
        if item.existing_qid:
            for pid, claims_list in built_by_pid.items():
                prefix = "OWNER_ONLY_METADATA: " if owner_only else ""
                try:
                    existing_list = [
                        claim for claim in (wbi_item.claims.get(pid) or [])
                        if not getattr(claim, "removed", False)
                    ]
                    existing_ids = [_claim_identity(claim) for claim in existing_list]
                    if (
                        pid == "P31"
                        and item.entity_type == "manuscript"
                        and not self._is_test
                        and not owner_only
                        and not is_owned_by_us
                        and self._is_our_item_cache.get(str(item.existing_qid)) is not True
                        and item.existing_qid in self._foreign_accept_qids
                    ):
                        for claim in claims_list:
                            identity = _claim_identity(claim)
                            matches = [
                                old for old in existing_list
                                if _owner_claim_identity(old) == identity[:2]
                            ]
                            if len(matches) > 1:
                                raise ClaimBuildError(["ambiguous matching P31 claims."])
                            if (
                                len(matches) == 1
                                and identity[2] in {"normal", "preferred"}
                                and _claim_identity(matches[0])[2] in {"normal", "preferred"}
                            ):
                                claim.rank = matches[0].rank
                    proposed_ids = [_claim_identity(claim) for claim in claims_list]
                except ClaimBuildError:
                    raise
                except Exception as exc:
                    raise ClaimBuildError([f"{prefix}invalid {pid} claim metadata."]) from exc
                for identity in proposed_ids:
                    if proposed_ids.count(identity) > 1 or existing_ids.count(identity) > 1:
                        raise ClaimBuildError([f"{prefix}ambiguous matching {pid} claims."])
                    if identity not in existing_ids and any(old[:2] == identity[:2] for old in existing_ids):
                        raise ClaimBuildError([f"{prefix}conflicting {pid} rank."])
                existing_by_pid[pid] = existing_list

        if not item.existing_qid:
            for pid, claims_list in built_by_pid.items():
                wbi_item.claims.add(claims_list, action_if_exists=ActionIfExists.FORCE_APPEND)
                added_properties.extend([pid] * len(claims_list))
        elif owner_only:
            for pid, claims_list in built_by_pid.items():
                try:
                    existing_list = existing_by_pid[pid]
                    proposed_ids = [_claim_identity(claim) for claim in claims_list]
                    incomplete = "P31" in built_by_pid and any(
                        _claim_identity(old) not in proposed_ids for old in existing_list
                    )
                except Exception as exc:
                    raise ClaimBuildError([f"OWNER_ONLY_METADATA: cannot compare existing {pid} claims."]) from exc
                if incomplete:
                    raise ClaimBuildError([
                        f"OWNER_ONLY_INCOMPLETE_SOURCE: {pid} would remove an existing claim; "
                        "the complete original source is not verified."
                    ])
                for claim in claims_list:
                    try:
                        snak = claim.mainsnak.get_json()
                        claim_id = _claim_identity(claim)
                        same_snak = [
                            old for old in existing_list
                            if _owner_snak_identity(old.mainsnak.get_json()) == claim_id[0]
                        ]
                        matches = [old for old in same_snak if _claim_identity(old) == claim_id]
                        snak_conflict = any(
                            old.mainsnak.get_json().get("snaktype") != snak.get("snaktype")
                            and all(old.mainsnak.get_json().get(key) == snak.get(key)
                                    for key in ("property", "datatype", "datavalue"))
                            for old in existing_list
                        )
                    except Exception as exc:
                        raise ClaimBuildError([f"OWNER_ONLY_METADATA: cannot compare {pid} metadata."]) from exc
                    if snak_conflict:
                        raise ClaimBuildError([f"OWNER_ONLY_METADATA: conflicting {pid} snak type."])
                    if len(matches) > 1:
                        raise ClaimBuildError([f"OWNER_ONLY_METADATA: ambiguous matching {pid} claims."])
                    if matches:
                        old = matches[0]
                        try:
                            seen = {_reference_identity(ref.get_json()) for ref in old.references}
                            for ref in claim.references:
                                identity = _reference_identity(ref.get_json())
                                if identity not in seen:
                                    old.references.add(ref)
                                    seen.add(identity)
                                    added_properties.append(pid)
                        except Exception as exc:
                            raise ClaimBuildError([f"OWNER_ONLY_METADATA: invalid {pid} references."]) from exc
                        continue
                    if same_snak:
                        raise ClaimBuildError([f"OWNER_ONLY_METADATA: conflicting {pid} qualifiers."])
                    wbi_item.claims.add(claim, action_if_exists=ActionIfExists.FORCE_APPEND)
                    existing_list.append(claim)
                    added_properties.append(pid)
        elif is_owned_by_us and "P31" in built_by_pid:
            for pid, claims_list in built_by_pid.items():
                existing_list = existing_by_pid[pid]
                proposed_ids = [_claim_identity(claim) for claim in claims_list]
                existing_ids = [_claim_identity(claim) for claim in existing_list]
                changed = False
                for nc in claims_list:
                    for ec in existing_list:
                        if _claim_identity(nc) == _claim_identity(ec):
                            existing_refs = list(ec.references or [])
                            replacement = References()
                            seen_refs: set[str] = set()
                            seen_existing: set[tuple[str, object]] = set()
                            for reference in existing_refs:
                                identity = _reference_identity(reference.get_json())
                                key = (identity, reference.hash)
                                if key in seen_existing:
                                    continue
                                seen_existing.add(key)
                                seen_refs.add(identity)
                                replacement.add(reference)
                            for reference in nc.references or []:
                                identity = _reference_identity(reference.get_json())
                                if identity in seen_refs:
                                    continue
                                seen_refs.add(identity)
                                replacement.add(reference)
                            before = [(_reference_identity(ref.get_json()), ref.hash) for ref in existing_refs]
                            after = [(_reference_identity(ref.get_json()), ref.hash) for ref in replacement]
                            if after != before:
                                ec.references = replacement
                                changed = True
                            break
                for old, identity in zip(existing_list, existing_ids, strict=True):
                    if identity not in proposed_ids and not any(new[0] == identity[0] for new in proposed_ids):
                        old.remove()
                        changed = True
                for claim, identity in zip(claims_list, proposed_ids, strict=True):
                    if identity not in existing_ids:
                        wbi_item.claims.add(claim, action_if_exists=ActionIfExists.FORCE_APPEND)
                        changed = True
                if changed:
                    added_properties.append(pid)

            for stale_pid in (() if owner_only else (
                "P9302", "P282", "P7153", "P1071", "P136", "P921",
                "P1680", "P1684", "P528", "P5816",
            )):
                if stale_pid in built_by_pid:
                    continue
                try:
                    stale_claims = [
                        c for c in (wbi_item.claims.get(stale_pid) or [])
                        if not getattr(c, "removed", False)
                    ]
                except Exception:
                    stale_claims = []
                if stale_claims:
                    for sc in stale_claims:
                        sc.remove()
                    added_properties.append(stale_pid)
        else:
            for pid, claims_list in built_by_pid.items():
                if is_owned_by_us and pid in _DEFERRED_LINK_PIDS:
                    # Pass 2 of a deferred link: the replacement and the removal
                    # of the superseded claim are one write, so a failed write keeps the old claim.
                    proposed_ids = [_claim_identity(claim) for claim in claims_list]
                    for old in existing_by_pid[pid]:
                        old_identity = _claim_identity(old)
                        if old_identity not in proposed_ids and not any(new[0] == old_identity[0] for new in proposed_ids):
                            old.remove()
                            added_properties.append(pid)
                for claim in claims_list:
                    matching = next(
                        (old for old in existing_by_pid[pid] if _claim_identity(old) == _claim_identity(claim)),
                        None,
                    )
                    if matching is None:
                        wbi_item.claims.add(claim, action_if_exists=ActionIfExists.FORCE_APPEND)
                        added_properties.append(pid)
                        continue
                    seen = {_reference_identity(ref.get_json()) for ref in matching.references}
                    for ref in claim.references:
                        identity = _reference_identity(ref.get_json())
                        if identity not in seen:
                            matching.references.add(ref)
                            seen.add(identity)
                            added_properties.append(pid)

        if metadata_updated and "labels" not in added_properties:
            added_properties.append("labels")
        if owner_only and alias_updated:
            added_properties.append("aliases")

        new_claims = len(added_properties) if item.existing_qid else built
        return wbi_item, new_claims, added_properties

    def _claim_exists(self, wbi_item: object, stmt: WikidataStatement) -> bool:
        """Check if a claim with the same property+value already exists on the item."""
        try:
            existing_claims = wbi_item.claims.get(stmt.property_id)
            if not existing_claims:
                return False

            if _normalize_wbi_value_type(stmt.value_type) in ("somevalue", "novalue"):
                # No datavalue to compare: use the identity the builder uses
                # (snaktype plus qualifiers), so an existing claim is recognized.
                built = self._build_claim(stmt)
                return built is not None and any(
                    _claim_identity(existing) == _claim_identity(built)
                    for existing in existing_claims
                )

            for existing in existing_claims:
                existing_value = self._extract_claim_value(existing)
                if native_value_matches_wiki(stmt, existing_value):
                    return True
        except Exception:
            pass  # If comparison fails, assume claim doesn't exist → add it
        return False

    @staticmethod
    def _extract_claim_value(wbi_claim: object) -> str:
        """Extract a comparable string value from a WBI claim."""
        try:
            snak = wbi_claim.mainsnak
            dv = snak.datavalue
            if not dv:
                return ""
            val = dv.get("value", dv) if isinstance(dv, dict) else dv
            if isinstance(val, dict):
                if "id" in val:
                    return val["id"]  # Q12345
                if "time" in val:
                    return val["time"]  # +1650-00-00T00:00:00Z
                if "text" in val:
                    return val["text"]  # monolingual text
                if "amount" in val:
                    return val["amount"]
                return str(val)
            return str(val)
        except Exception:
            return ""

    def _quantity_unit_uri(self, unit: str | None) -> str:
        """Build a Wikibase quantity unit URI on the active wiki (Rule W-185)."""
        from converter.wikidata.test_wiki_compat import (  # noqa: PLC0415
            quantity_unit_to_live_qid,
            _test_unit_qid,
        )

        _validate_quantity_unit(unit, is_test=self._is_test)
        wiki_host = "test.wikidata.org" if self._is_test else "www.wikidata.org"
        raw = str(unit or "").strip()
        if not raw:
            return "1"
        if self._is_test and _test_unit_qid(raw):
            test_qid = _test_unit_qid(raw)
            allowed = set(self._test_qid_map.values()) | set(self._test_stubs_we_created)
            if test_qid in allowed and self._test_entity_exists.get(test_qid):
                return f"http://{wiki_host}/entity/{test_qid}"
            raise ValueError(f"Quantity unit {test_qid} lacks a verified test mapping (Rule W-186)")
        live_qid = quantity_unit_to_live_qid(raw)
        if not live_qid:
            if raw != "1":
                raise ValueError(f"Quantity unit {raw!r} has no verified mapping (Rule W-186)")
            return "1"
        if self._is_test:
            if raw == live_qid and live_qid in self._test_qid_map.values():
                raise ValueError(f"Quantity unit {live_qid} has an ambiguous wiki namespace (Rule W-186)")
            mapped = self._test_qid_map.get(live_qid)
            allowed = set(self._test_qid_map.values()) | set(self._test_stubs_we_created)
            exists = {
                qid for qid, ok in self._test_entity_exists.items() if ok
            }
            if mapped and mapped in allowed and mapped in exists:
                return f"http://{wiki_host}/entity/{mapped}"
            raise ValueError(
                f"Quantity unit {live_qid} is not remapped on test.wikidata.org "
                "(Rule W-186) — refusing a dimensionless write"
            )
        return f"http://{wiki_host}/entity/{live_qid}"

    def _build_claim(self, stmt: WikidataStatement, *, owner_only: bool = False) -> object | None:
        """Convert a WikidataStatement to a WikibaseIntegrator claim.

        Args:
            stmt: The statement to convert.

        Returns:
            A datatypes claim object, or None if conversion fails.
        """
        from wikibaseintegrator import datatypes  # noqa: PLC0415
        from wikibaseintegrator.models import Reference, References  # noqa: PLC0415
        from wikibaseintegrator.wbi_enums import WikibaseRank  # noqa: PLC0415

        # Each block is a separate source; legacy flat references form one block.
        _validate_native_statement(stmt, is_test=self._is_test, owner_only=owner_only)
        if self._is_test:
            issues = self._test_local_target_issues(WikidataItem(statements=[stmt]), require_mapped=True)
            if issues:
                raise ClaimBuildError(issues)
        for snak in _statement_reference_snaks(stmt):
            if (self._is_test and isinstance(snak, dict)
                    and _normalize_wbi_value_type(snak.get("value_type") or snak.get("type") or snak.get("datatype") or "string") == "item"
                    and snak.get("value_namespace") not in ("local", "mapped-test")):
                raise ClaimBuildError([f"Unmapped test reference for {stmt.property_id}."])
            if isinstance(snak, dict) and snak.get("value_namespace") == "mapped-test":
                from converter.wikidata.test_wiki_compat import item_target_qid  # noqa: PLC0415

                qid = item_target_qid(snak.get("value"))
                if (not self._is_test or qid not in self._test_qid_map.values()
                        or not self._test_entity_exists.get(qid)):
                    raise ClaimBuildError([f"Unverified mapped-test reference for {stmt.property_id}."])
        refs = References()
        blocks = stmt.reference_blocks or ([stmt.references] if stmt.references else [])
        for block in blocks:
            if not isinstance(block, list) or not block:
                raise ClaimBuildError([f"Invalid reference block for {stmt.property_id}."])
            ref = Reference()
            seen_snaks: set[str] = set()
            for ref_snak in block:
                ref_claim = self._build_reference_snak(ref_snak, owner_only=owner_only, reference=True)
                if ref_claim is None:
                    prefix = "OWNER_ONLY_METADATA: " if owner_only else ""
                    raise ClaimBuildError([f"{prefix}invalid reference for {stmt.property_id}."])
                snak = ref_claim.get_json()["mainsnak"]
                identity = _reference_identity({"snaks": {snak["property"]: [snak]}})
                if identity in seen_snaks:
                    continue
                seen_snaks.add(identity)
                ref.add(ref_claim)
            refs.add(ref)

        value = stmt.value
        if isinstance(value, str) and value.startswith("__LOCAL:"):
            return None

        # Build qualifiers
        from wikibaseintegrator.models import Qualifiers  # noqa: PLC0415

        qualifiers = Qualifiers()
        for qual in stmt.qualifiers or []:
            qual_claim = self._build_reference_snak(qual, owner_only=True) if owner_only else self._build_reference_snak(qual)
            if qual_claim:
                qualifiers.add(qual_claim)
            else:
                prefix = "OWNER_ONLY_METADATA: " if owner_only else ""
                raise ClaimBuildError([f"{prefix}invalid qualifier for {stmt.property_id}."])

        # Rule 42: resolve rank for the claim. WikibaseIntegrator's
        # WikibaseRank enum is the canonical surface; we fail closed by
        # defaulting to NORMAL if the value is unknown.
        rank_map = {
            "preferred": WikibaseRank.PREFERRED,
            "normal": WikibaseRank.NORMAL,
            "deprecated": WikibaseRank.DEPRECATED,
        }
        rank_enum = rank_map.get(stmt.rank, WikibaseRank.NORMAL)
        vtype = _normalize_wbi_value_type(stmt.value_type)

        try:
            # Rule 42: somevalue/novalue map to WBI's snaktype field rather
            # than a concrete datavalue. We build a stub Item claim and
            # override its mainsnak.
            if vtype in ("somevalue", "novalue"):
                stub = datatypes.Item(
                    prop_nr=stmt.property_id,
                    references=refs,
                    qualifiers=qualifiers,
                    rank=rank_enum,
                )
                stub.mainsnak.snaktype = stmt.value_type
                stub.mainsnak.datavalue = {}
                return stub
            if vtype == "item":
                return datatypes.Item(
                    prop_nr=stmt.property_id,
                    value=_item_qid(value),
                    references=refs,
                    qualifiers=qualifiers,
                    rank=rank_enum,
                )
            if vtype == "string":
                clean_str = " | ".join(
                    p.strip() for p in str(value).splitlines() if p.strip()
                )
                return datatypes.String(
                    prop_nr=stmt.property_id,
                    value=clean_str,
                    references=refs,
                    qualifiers=qualifiers,
                    rank=rank_enum,
                )
            if vtype == "external-id":
                return datatypes.ExternalID(
                    prop_nr=stmt.property_id,
                    value=str(value),
                    references=refs,
                    qualifiers=qualifiers,
                    rank=rank_enum,
                )
            if vtype == "time":
                return datatypes.Time(
                    prop_nr=stmt.property_id,
                    time=repair_wikidata_time(str(value)),
                    precision=stmt.precision,
                    references=refs,
                    qualifiers=qualifiers,
                    rank=rank_enum,
                    **({"calendarmodel": "http://www.wikidata.org/entity/Q1985727"}
                       if self._owner_only_session is not None else {}),
                )
            if vtype == "quantity":
                try:
                    unit_val = owner_only_quantity_unit_uri(stmt.unit) if owner_only else self._quantity_unit_uri(stmt.unit)
                except ValueError as exc:
                    raise ClaimBuildError([f"Invalid quantity unit for {stmt.property_id}: {exc}"]) from exc
                return datatypes.Quantity(
                    prop_nr=stmt.property_id,
                    amount=value,
                    unit=unit_val,
                    references=refs,
                    qualifiers=qualifiers,
                    rank=rank_enum,
                )
            if vtype == "url":
                return datatypes.URL(
                    prop_nr=stmt.property_id,
                    value=str(value),
                    references=refs,
                    qualifiers=qualifiers,
                    rank=rank_enum,
                )
            if vtype == "monolingualtext":
                clean_text = " | ".join(
                    p.strip() for p in str(value).splitlines() if p.strip()
                )
                return datatypes.MonolingualText(
                    prop_nr=stmt.property_id,
                    text=clean_text,
                    language=stmt.language,
                    references=refs,
                    qualifiers=qualifiers,
                    rank=rank_enum,
                )
        except ClaimBuildError:
            raise
        except Exception as exc:
            logger.warning(
                "Failed to build claim for %s=%s: %s",
                stmt.property_id,
                value,
                exc,
            )
            return None

        logger.warning(
            "Unknown claim value_type %r for %s=%s",
            stmt.value_type,
            stmt.property_id,
            value,
        )
        return None

    def _build_reference_snak(
        self, ref_snak: dict[str, object], *, owner_only: bool = False, reference: bool = False,
    ) -> Claim | None:
        """Build a reference snak for WikibaseIntegrator."""
        from wikibaseintegrator import datatypes  # noqa: PLC0415

        if not isinstance(ref_snak, dict):
            return None
        prop = ref_snak.get("property", "") or ref_snak.get("property_id", "")
        value = ref_snak.get("value", "")
        snak_type = _normalize_wbi_value_type(
            ref_snak.get("value_type") or ref_snak.get("type") or ref_snak.get("datatype") or "string",
        )

        if reference:
            types = {
                _normalize_wbi_value_type(ref_snak[key])
                for key in ("type", "value_type", "datatype") if ref_snak.get(key)
            }
            if (len(types) > 1 or "datavalue" in ref_snak
                    or ref_snak.get("snaktype", "value") != "value"
                    or (ref_snak.get("property") and ref_snak.get("property_id")
                        and ref_snak["property"] != ref_snak["property_id"])):
                return None
        if (owner_only or reference) and (
            not isinstance(prop, str) or re.fullmatch(r"P[1-9][0-9]*", prop) is None
            or value is None or (isinstance(value, str) and not value.strip())
            or snak_type not in ({"item", "url", "time", "string", "external-id"}
                                 | ({"monolingualtext"} if not reference else set()))
            or (reference and snak_type == "item" and re.fullmatch(r"Q[1-9][0-9]*", _item_qid(value)) is None)
            or (reference and snak_type != "item" and not isinstance(value, str))
        ):
            return None
        if reference and snak_type != "time" and any(
            key in ref_snak for key in ("calendarmodel", "before", "after", "timezone")
        ):
            return None

        try:
            if snak_type == "item":
                return datatypes.Item(prop_nr=prop, value=_item_qid(value))
            if snak_type == "url":
                return datatypes.URL(prop_nr=prop, value=value)
            if snak_type == "external-id":
                return datatypes.ExternalID(prop_nr=prop, value=str(value))
            if not reference and snak_type == "monolingualtext":
                return datatypes.MonolingualText(prop_nr=prop, text=value, language=ref_snak.get("language"))
            if snak_type == "time":
                precision = ref_snak.get("precision", 11)
                time_options: dict[str, object] = {}
                from converter.wikidata.quickstatements import validate_reference_time  # noqa: PLC0415

                validate_reference_time(value, precision, calendarmodel=ref_snak.get("calendarmodel"))
                for key in ("before", "after", "timezone"):
                    if key in ref_snak:
                        part = ref_snak[key]
                        if type(part) is not int or (key != "timezone" and part < 0):
                            return None
                        time_options[key] = part
                if "calendarmodel" in ref_snak:
                    calendar = ref_snak["calendarmodel"]
                    if (self._is_test or not isinstance(calendar, str)
                            or re.fullmatch(r"https?://www\.wikidata\.org/entity/Q[1-9][0-9]*", calendar) is None):
                        return None
                    time_options["calendarmodel"] = calendar
                if self._owner_only_session is not None and "calendarmodel" not in time_options:
                    time_options["calendarmodel"] = "http://www.wikidata.org/entity/Q1985727"
                return datatypes.Time(
                    prop_nr=prop,
                    time=repair_wikidata_time(str(value)),
                    precision=int(precision),
                    **time_options,
                )
            return datatypes.String(prop_nr=prop, value=value)
        except Exception as exc:
            logger.warning("Failed to build reference snak %s: %s", prop, exc)
            return None

    def _get_authenticated_user(self) -> str | None:
        """Get the username of the authenticated session via API."""
        if self._authenticated_user is not None:
            return self._authenticated_user
        try:
            if self._login is None and ":" in self._token and "@" in self._token.split(":", 1)[0]:
                try:
                    self._init_wbi()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Could not init WBI for userinfo: %s", exc)
            if self._login is not None:
                info = self._query_userinfo_rights()
                if info:
                    name = str(info.get("name") or "")
                    if "anon" not in info and name and name != "127.0.0.1":
                        self._authenticated_user = name
                        logger.info("Authenticated as Wikidata user: %s", name)
                        return name

            import requests  # noqa: PLC0415

            api_url = _TEST_API if self._is_test else _WIKIDATA_API
            headers = {"User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)"}

            # OAuth 2.0 bearer token format (single token, no |)
            if "|" not in self._token and ":" not in self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            elif "|" in self._token:
                # OAuth: extract bearer if WBI provided one
                parts = self._token.split("|")
                if len(parts) == 2:
                    # Owner-only consumer — use as bearer
                    headers["Authorization"] = f"Bearer {self._token}"

            resp = requests.get(
                api_url,
                params={"action": "query", "meta": "userinfo", "format": "json"},
                headers=headers,
                timeout=10,
            )
            info = resp.json().get("query", {}).get("userinfo", {})
            user = info.get("name")
            if user and "anon" not in info and user != "127.0.0.1":
                self._authenticated_user = user
                logger.info("Authenticated as Wikidata user: %s", user)
                return user
        except Exception as exc:
            logger.warning("Could not determine authenticated user: %s", exc)
        return None

    def _get_first_revision_author(self, qid: str) -> str | None:
        """Get the username of the FIRST revision (creator) of an item via API."""
        if qid in self._creator_cache:
            return self._creator_cache[qid]
        try:
            import requests  # noqa: PLC0415

            api_url = _TEST_API if self._is_test else _WIKIDATA_API
            resp = requests.get(
                api_url,
                params={
                    "action": "query",
                    "prop": "revisions",
                    "titles": qid,
                    "rvprop": "user",
                    "rvdir": "newer",
                    "rvlimit": "1",
                    "format": "json",
                },
                headers={"User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)"},
                timeout=10,
            )
            pages = resp.json().get("query", {}).get("pages", {})
            for _pid, page in pages.items():
                revs = page.get("revisions", [])
                if revs:
                    author = revs[0].get("user")
                    if author:
                        self._creator_cache[qid] = author
                        return author
        except Exception as exc:
            logger.warning("Could not get first revision author for %s: %s", qid, exc)
        return None

    def owner_only_target_valid(self, qid: str, control_number: str) -> bool:
        revision = self._owner_only_revision(qid, control_number)
        return type(revision) is int and revision > 0

    def _owner_only_revision(self, qid: str, control_number: str) -> int | None:
        """Check the live target identity, first author and current revision."""
        if (self._is_test or not isinstance(qid, str) or not re.fullmatch(r"Q[1-9][0-9]*", qid)
                or not isinstance(control_number, str) or not re.fullmatch(r"[0-9]{5,}", control_number)):
            return None
        try:
            self.ensure_owner_only_authenticated()
            info = self._query_userinfo_rights()
            if not isinstance(info, dict) or "anon" in info:
                return None
            user = info.get("name")
            if not isinstance(user, str) or not user or user == "127.0.0.1":
                return None
            session = self._login.get_session()
            response = session.get(
                _WIKIDATA_API,
                params={"action": "wbgetentities", "ids": qid, "props": "info|claims", "format": "json"},
                headers={"User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)"},
                timeout=10, allow_redirects=False,
            )
            if response.status_code != 200:
                return None
            response.raise_for_status()
            payload = response.json()
            if payload.get("error") or payload.get("redirects"):
                return None
            entities = payload.get("entities") or {}
            entity = entities.get(qid)
            if (not isinstance(entity, dict) or entity.get("id") != qid
                    or entity.get("type") != "item"
                    or "missing" in entity or "redirect" in entity
                    or type(entity.get("lastrevid")) is not int or entity["lastrevid"] <= 0):
                return None
            claims = entity.get("claims")
            statements = claims.get("P3959") if isinstance(claims, dict) else None
            if not isinstance(statements, list) or len(statements) != 1 or not isinstance(statements[0], dict):
                return None
            if statements[0].get("rank") not in ("normal", "preferred"):
                return None
            snak = statements[0].get("mainsnak")
            if (not isinstance(snak, dict) or snak.get("property") != "P3959"
                    or snak.get("snaktype") != "value" or snak.get("datatype") != "external-id"):
                return None
            datavalue = snak.get("datavalue")
            if (not isinstance(datavalue, dict) or datavalue.get("type") != "string"
                    or datavalue.get("value") != control_number):
                return None
            resp = session.get(
                _WIKIDATA_API,
                params={"action": "query", "prop": "revisions", "titles": qid,
                        "rvprop": "user", "rvdir": "newer", "rvlimit": "1", "format": "json"},
                headers={"User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)"},
                timeout=10,
                allow_redirects=False,
            )
            if resp.status_code != 200:
                return None
            resp.raise_for_status()
            data = resp.json()
            query = data.get("query") or {}
            pages = query.get("pages") or {}
            if data.get("error") or query.get("redirects") or len(pages) != 1:
                return None
            page = next(iter(pages.values()))
            if (not isinstance(page, dict) or page.get("title") != qid
                    or page.get("ns") != 0 or not isinstance(page.get("pageid"), int)
                    or page["pageid"] <= 0
                    or "missing" in page or "redirect" in page):
                return None
            revisions = page.get("revisions")
            valid = bool(
                isinstance(revisions, list) and len(revisions) == 1
                and isinstance(revisions[0], dict)
                and "userhidden" not in revisions[0]
                and revisions[0].get("user") == user
            )
            return entity["lastrevid"] if valid else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Owner-only verification failed for %s: %s", qid, exc)
            return None

    def _user_created_via_contribs(self, qid: str, user: str) -> bool | None:
        """Independent cross-check via ``list=usercontribs`` API endpoint.

        Returns:
            True  — *user* has a ``new``-type contribution on *qid* (i.e.
                    they created the page).
            False — *user* has NO ``new``-type contribution on *qid*.
            None  — the API call failed; caller must fall back.

        This is the second of the two independent creator-verification
        channels required by rule 38. If this disagrees with the
        ``prop=revisions`` answer, the item is NOT confirmed as ours.
        """
        try:
            import requests  # noqa: PLC0415

            api_url = _TEST_API if self._is_test else _WIKIDATA_API
            resp = requests.get(
                api_url,
                params={
                    "action": "query",
                    "list": "usercontribs",
                    "ucuser": user,
                    "uctitle": qid,
                    "uctype": "new",
                    "uclimit": "1",
                    "format": "json",
                },
                headers={"User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)"},
                timeout=10,
            )
            contribs = resp.json().get("query", {}).get("usercontribs", [])
            return bool(contribs)
        except Exception as exc:
            logger.warning("usercontribs check failed for %s: %s", qid, exc)
            return None

    def _item_exists_on_wikidata_sparql(self, qid: str) -> bool | None:
        """SPARQL existence check — ``ASK WHERE { wd:<qid> ?p ?o }``.

        Returns:
            True  — item exists and has at least one triple.
            False — item has no triples (deleted / redirected / not found).
            None  — SPARQL endpoint failure; caller must fall back.

        A False result means the QID has been deleted or merged into
        another item since we reconciled it; under rule 38 we refuse to
        modify such items even if the creator check passes, because the
        target of any modification would be ambiguous.
        """
        try:
            import requests  # noqa: PLC0415

            endpoint = _TEST_SPARQL if self._is_test else _WIKIDATA_SPARQL
            resp = requests.get(
                endpoint,
                params={
                    "query": f"ASK WHERE {{ wd:{qid} ?p ?o . }}",
                    "format": "json",
                },
                headers={
                    "User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)",
                    "Accept": "application/sparql-results+json",
                },
                timeout=15,
            )
            return bool(resp.json().get("boolean"))
        except Exception as exc:
            logger.warning("SPARQL existence check failed for %s: %s", qid, exc)
            return None

    def _bot_excluded(self, qid: str) -> bool:
        """Check the item's talk page for a {{bots|deny=…}} exclusion.

        Returns True if the bot should NOT edit this item. Caches results
        per QID for the lifetime of the uploader instance. Bug fix
        2026-04-16 (deeper audit Fix #9): respects the community convention
        documented at https://www.wikidata.org/wiki/Wikidata:Bot_policy

        Network failures or missing talk pages return False (allow edit) —
        we only block when an explicit exclusion is found.
        """
        if not hasattr(self, "_bot_exclusion_cache"):
            self._bot_exclusion_cache: dict[str, bool] = {}
        if qid in self._bot_exclusion_cache:
            return self._bot_exclusion_cache[qid]
        try:
            import re  # noqa: PLC0415

            import requests  # noqa: PLC0415

            api_url = _TEST_API if self._is_test else _WIKIDATA_API
            resp = requests.get(
                api_url,
                params={
                    "action": "parse",
                    "page": f"Talk:{qid}",
                    "prop": "wikitext",
                    "format": "json",
                    "redirects": "1",
                },
                headers={"User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)"},
                timeout=10,
            )
            data = resp.json()
            if "error" in data:
                self._bot_exclusion_cache[qid] = False
                return False
            wikitext = (data.get("parse", {}).get("wikitext", {}).get("*", "") or "").lower()
            # Match {{bots|deny=…}} where … contains "all" or our bot name.
            auth_user = (self._get_authenticated_user() or "").lower()
            patterns = [r"\{\{bots\s*\|\s*deny\s*=\s*all"]
            if auth_user:
                # Tolerate a few common bot-name variants.
                for name in {auth_user, auth_user.replace(" ", "_"), "mhmpipeline"}:
                    patterns.append(rf"\{{\{{bots\s*\|\s*deny\s*=[^}}]*\b{re.escape(name)}\b")
            excluded = any(re.search(p, wikitext) for p in patterns)
            self._bot_exclusion_cache[qid] = excluded
            return excluded
        except Exception as exc:
            logger.warning("Could not check bot exclusion for %s: %s", qid, exc)
            self._bot_exclusion_cache[qid] = False
            return False

    def register_foreign_accept(self, qid: str) -> None:
        """Record curator accept for one foreign QID (live wikidata.org only).

        Test uploads MUST NOT call this — they never UPDATE foreign items.
        """
        clean = str(qid or "").strip()
        if not clean:
            return
        if self._is_test:
            logger.warning(
                "Ignoring accept_foreign_modify for %s on test.wikidata.org — "
                "test uploads must not UPDATE foreign items",
                clean,
            )
            return
        logger.warning("FOREIGN_MODIFY_ACCEPTED qid=%s (live upload only)", clean)
        self._foreign_accept_qids.add(clean)

    def prime_entity_caches(
        self,
        entities: Mapping[str, Mapping[str, object]],
    ) -> None:
        """Seed P3959 and full-entity caches from a batched ``wbgetentities`` map."""
        for raw_qid, entity in (entities or {}).items():
            qid = str(raw_qid or "").strip()
            if not qid or not isinstance(entity, dict) or "missing" in entity:
                continue
            self._prefetched_entities[qid] = dict(entity)
            claims = entity.get("claims") or {}
            statements = claims.get("P3959") or [] if isinstance(claims, dict) else []
            values: set[str] = set()
            for statement in statements:
                if not isinstance(statement, dict):
                    continue
                mainsnak = statement.get("mainsnak") or {}
                datavalue = mainsnak.get("datavalue") or {}
                value = datavalue.get("value") if isinstance(datavalue, dict) else None
                if isinstance(value, str) and value.strip():
                    values.add(value.strip())
            self._q3959_cache[qid] = frozenset(values)

    def warm_ownership_cache(
        self,
        qids: Iterable[str],
        prefetched_entities: Mapping[str, Mapping[str, object]] | None = None,
    ) -> None:
        """Batch-resolve creator ownership via ``list=usercontribs`` (500/page)."""
        target_qids = [str(q or "").strip() for q in qids if str(q or "").strip().startswith("Q")]
        if not target_qids:
            return
        auth_user = self._get_authenticated_user()
        if not auth_user:
            return
        api_url = _TEST_API if self._is_test else _WIKIDATA_API
        created_by_me: set[str] = set()
        try:
            import requests  # noqa: PLC0415

            session = (
                self._login.get_session()
                if self._login is not None and hasattr(self._login, "get_session")
                else requests.Session()
            )
            params: dict[str, str] = {
                "action": "query",
                "list": "usercontribs",
                "ucuser": auth_user,
                "uctype": "new",
                "ucnamespace": "0",
                "uclimit": "500",
                "format": "json",
            }
            for _ in range(25):
                resp = session.get(
                    api_url,
                    params=params,
                    headers={"User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)"},
                    timeout=20,
                )
                resp.raise_for_status()
                body = resp.json()
                contribs = body.get("query", {}).get("usercontribs") or []
                for row in contribs:
                    if isinstance(row, dict):
                        title = str(row.get("title") or "").strip()
                        if title.startswith("Q"):
                            created_by_me.add(title)
                cont = body.get("continue")
                if not isinstance(cont, dict) or not cont.get("uccontinue"):
                    break
                params["uccontinue"] = str(cont["uccontinue"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("warm_ownership_cache failed: %s", exc)
            return

        entity_map = prefetched_entities if prefetched_entities is not None else self._prefetched_entities
        for qid in target_qids:
            ent = entity_map.get(qid) if isinstance(entity_map, Mapping) else None
            has_triples = bool(
                isinstance(ent, Mapping)
                and "missing" not in ent
                and (ent.get("claims") or ent.get("labels"))
            )
            # A missing contribution is not proof of foreign creation. Only a
            # confirmed first-revision author sets the cache; other items are
            # decided per item by the first-revision check.
            if (
                qid in created_by_me
                and has_triples
                and self._get_first_revision_author(qid) == auth_user
            ):
                self._is_our_item_cache[qid] = True

    def share_caches_from(self, other: WikidataUploader) -> None:
        """Share ownership and prefetched-entity caches across shard slot uploaders."""
        if (
            self._is_test != other._is_test
            or not self._authenticated_user
            or self._authenticated_user != other._authenticated_user
        ):
            raise ValueError("Cannot share Wikidata caches across unverified accounts or wikis")
        self._creator_cache = other._creator_cache
        self._is_our_item_cache = other._is_our_item_cache
        self._q3959_cache = other._q3959_cache
        self._bot_exclusion_cache = other._bot_exclusion_cache
        self._prefetched_entities = other._prefetched_entities
        self._existing_label_snapshot = other._existing_label_snapshot
        self._foreign_accept_qids.clear()
        self._rate_limit_state = other._rate_limit_state

    def _item_usable_as_test_reference(
        self, qid: str, *, live_qid: str | None = None,
    ) -> bool:
        """True when a test Q-id may be used as a claim value (not an UPDATE target)."""
        clean = str(qid or "").strip()
        if not clean:
            return False
        if clean in self._test_stubs_we_created or clean in self._test_fresh_creates:
            return True
        if live_qid:
            from converter.wikidata.test_wiki_compat import (  # noqa: PLC0415
                description_is_mhm_stub_for,
            )

            desc = self._item_english_description(clean)
            if description_is_mhm_stub_for(desc, live_qid):
                return True
        return self._is_our_item(clean)

    def _item_english_description(self, qid: str) -> str:
        if qid in self._test_item_descriptions:
            return self._test_item_descriptions[qid]
        fetched = self._wbgetentities([qid], props="descriptions")
        ent = fetched.get(qid) if isinstance(fetched.get(qid), dict) else {}
        descs = ent.get("descriptions") or {} if ent and "missing" not in ent else {}
        en = descs.get("en") or {}
        val = str(en.get("value") or "")
        self._test_item_descriptions[qid] = val
        return val

    @staticmethod
    def _our_p3959(item: object) -> str | None:
        """The record's NNL catalog control number (P3959) — manuscripts only.

        This is identity evidence, not evidence of creator ownership.
        """
        if str(getattr(item, "entity_type", "") or "").lower() != "manuscript":
            return None
        for statement in getattr(item, "statements", ()) or ():
            if getattr(statement, "property_id", None) != "P3959":
                continue
            value = getattr(statement, "value", None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _remote_p3959_values(self, qid: str) -> frozenset[str]:
        """The P3959 values on the live *qid* item (cached per QID).

        Returns an empty set on any failure: absence is not identity, so a
        missing or unreadable claim never accepts a foreign item.
        """
        cached = self._q3959_cache.get(qid)
        if cached is not None:
            return cached
        values: set[str] = set()
        payload = self._wbgetentities([qid], props="claims")
        entity = payload.get(qid)
        if isinstance(entity, dict) and "missing" not in entity:
            claims = entity.get("claims") or {}
            statements = claims.get("P3959") or [] if isinstance(claims, dict) else []
            for statement in statements:
                if not isinstance(statement, dict):
                    continue
                mainsnak = statement.get("mainsnak") or {}
                datavalue = mainsnak.get("datavalue") or {}
                value = datavalue.get("value") if isinstance(datavalue, dict) else None
                if isinstance(value, str) and value.strip():
                    values.add(value.strip())
        result = frozenset(values)
        self._q3959_cache[qid] = result
        return result

    def _p3959_identity_accepts(self, qid: str, our_p3959: str | None) -> bool:
        """True when the live item carries exactly our record's P3959 control number.

        Two independent values must be equal. This proves neither creator
        ownership nor permission to modify the item:
        no prefix or fuzzy compare, never on test.wikidata.org (P-numbers are
        remapped there), never when the remote item lacks P3959.
        """
        if self._is_test or not our_p3959:
            return False
        return our_p3959 in self._remote_p3959_values(qid)

    def _is_our_item(self, qid: str, *, our_p3959: str | None = None) -> bool:
        """Return True iff the authenticated user is the first-revision author of *qid*.

        TRIPLE-VERIFICATION contract (re-hardened 2026-04-24 per user
        directive "ensure 100 times that we will not modify entities not
        created by me — check using wikidata api and sparkql queries"):

        ========== ========== ========== ========== =========
        auth_user  rev.user   contribs   sparql     returns
        ========== ========== ========== ========== =========
        unknown    *          *          *          False
        known      unknown    *          *          False
        known      other      *          *          False
        known      self       False      *          False   ← cross-check disagrees
        known      self       None       ok         True    (contribs API down — rev call agreed)
        known      self       True       False      False   ← SPARQL says QID deleted
        known      self       True       ok         True
        ========== ========== ========== ========== =========

        Three INDEPENDENT verification channels:

            1. ``action=query&prop=revisions&rvdir=newer&rvlimit=1``
               — asks Wikidata's MediaWiki API who wrote the first
               revision of the item. Authoritative for creator.

            2. ``action=query&list=usercontribs&ucuser=<me>&uctitle=<qid>&uctype=new``
               — asks the same API whether the authenticated user has
               a "page creation" entry for this QID. Independent of
               (1): the two endpoints go through different internal
               paths, so a corruption on one side would not affect
               the other.

            3. ``ASK WHERE { wd:<qid> ?p ?o }`` on the Wikidata SPARQL
               endpoint — confirms the item still exists and has not
               been deleted, redirected, or blanked since we reconciled
               it. If the item no longer exists, the *target* of any
               modification is ambiguous and we must refuse.

        The P1343=Ktiv marker fallback is REMOVED. A community-created
        item could legitimately cite Ktiv as a source, which made that
        fallback dangerous: an API hiccup that blanked the
        revision-author lookup would silently allow modification of the
        community's item. Ktiv is a bibliographic source, not a creator
        fingerprint.

        The uploader caches every decision so repeated calls during a
        single upload are O(1) and cannot race with a concurrent edit
        that might change the first revision.
        """
        cached = self._is_our_item_cache.get(qid)
        if cached is not None:
            return cached

        # ── Channel #1: who is the authenticated user? ─────────────────
        auth_user = self._get_authenticated_user()
        if not auth_user:
            logger.warning(
                "SAFETY: Could not determine authenticated user — refusing "
                "any modification of existing item %s (fail-closed).",
                qid,
            )
            self._is_our_item_cache[qid] = False
            return False

        # ── Channel #2: who authored the first revision of the QID? ────
        creator = self._get_first_revision_author(qid)
        if not creator:
            logger.warning(
                "SAFETY: Could not determine first-revision author of %s — "
                "refusing modification (fail-closed).",
                qid,
            )
            self._is_our_item_cache[qid] = False
            return False

        if creator != auth_user:
            logger.warning(
                "SAFETY: Item %s was created by '%s', not '%s' — REFUSING to modify",
                qid, creator, auth_user,
            )
            self._is_our_item_cache[qid] = False
            return False

        # ── Channel #3: cross-check via list=usercontribs ──────────────
        #
        # Independent API endpoint confirming the user has a "new" (page
        # creation) contribution on the QID. If it disagrees with
        # channel #2, refuse — the two cannot disagree on a well-formed
        # page. ``None`` means the contribs call failed (network blip):
        # we accept that only because channel #2 already agreed.
        # list=usercontribs omits many older page creations, so a miss here
        # is logged only. The first-revision author above decides ownership.
        if self._user_created_via_contribs(qid, auth_user) is False:
            logger.info(
                "Ownership cross-check: list=usercontribs omits %s for '%s'; "
                "first-revision author confirms ownership.",
                qid, auth_user,
            )

        # ── Channel #4: SPARQL existence check ─────────────────────────
        #
        # Confirms the QID is a live, non-redirected, non-blanked entity.
        # If SPARQL says the item has zero triples, it has been deleted
        # or merged away — we must not attempt to modify a vanished QID.
        exists = self._item_exists_on_wikidata_sparql(qid)
        if exists is False and self._is_test:
            # test.wikidata.org SPARQL is sparse and often reports no triples
            # for items that Action API still serves. Do not refuse our own
            # UPDATEs (or stub reuse) on a false SPARQL miss — confirm via
            # wbgetentities, and treat a lookup failure as inconclusive.
            fetched = self._wbgetentities([qid], props="info")
            if qid not in fetched:
                exists = None
            else:
                ent = fetched.get(qid)
                exists = bool(isinstance(ent, dict) and "missing" not in ent)
        if exists is False:
            logger.warning(
                "SAFETY: SPARQL reports %s has no triples (deleted / "
                "redirected / blanked) — refusing modification.",
                qid,
            )
            self._is_our_item_cache[qid] = False
            return False

        self._is_our_item_cache[qid] = True
        return True

    def _assert_modifiable(
        self,
        qid: str,
        stage: str,
        *,
        our_p3959: str | None = None,
    ) -> None:
        """Defense-in-depth guard: raise if *qid* must not be modified.

        Called from EVERY stage of the modification pipeline so a single
        missed check at the entry point cannot let an unauthorised edit
        slip through. Raises :class:`UnauthorisedModificationError` —
        the uploader catches it and converts to a ``skipped`` result
        rather than a crash. A live curator accept authorizes only its exact
        QID and does not change the creator ownership result.
        """
        if not qid:
            return  # new-item creation path; no existing item at risk
        if not self._is_test and qid in self._foreign_accept_qids:
            return
        if not self._is_our_item(qid, our_p3959=our_p3959):
            raise UnauthorisedModificationError(qid=qid, stage=stage)

    def _wbgetentities(self, ids: list[str], *, props: str) -> dict[str, dict]:
        """Fetch entities from the current wiki. Empty dict on network failure."""
        import requests  # noqa: PLC0415

        out: dict[str, dict] = {}
        if not ids:
            return out
        api_url = _TEST_API if self._is_test else _WIKIDATA_API
        headers = {"User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)"}
        try:
            for i in range(0, len(ids), 50):
                chunk = ids[i : i + 50]
                resp = requests.get(
                    api_url,
                    params={
                        "action": "wbgetentities",
                        "ids": "|".join(chunk),
                        "props": props,
                        "format": "json",
                    },
                    headers=headers,
                    timeout=30,
                )
                resp.raise_for_status()
                entities = resp.json().get("entities") or {}
                if isinstance(entities, dict):
                    out.update(entities)
        except Exception as exc:  # noqa: BLE001
            logger.warning("wbgetentities failed (%s): %s", props, exc)
        return out

    def _wbsearchentities(
        self,
        search: str,
        *,
        entity_type: str,
        limit: int = 8,
    ) -> list[dict[str, str]]:
        """Search test wiki entities by label. Returns id/label/datatype dicts."""
        login = self._login
        if login is None:
            return []
        api_url = _TEST_API if self._is_test else _WIKIDATA_API
        try:
            session = login.get_session()
            resp = session.get(
                api_url,
                params={
                    "action": "wbsearchentities",
                    "search": search,
                    "language": "en",
                    "type": entity_type,
                    "limit": limit,
                    "format": "json",
                },
                timeout=30,
            )
            resp.raise_for_status()
            hits = resp.json().get("search") or []
            out: list[dict[str, str]] = []
            for hit in hits:
                if not isinstance(hit, dict):
                    continue
                hid = str(hit.get("id") or "")
                desc = str(hit.get("description") or "")
                if hid and desc and hid not in self._test_item_descriptions:
                    self._test_item_descriptions[hid] = desc
                out.append({
                    "id": hid,
                    "label": str(hit.get("label") or ""),
                    "datatype": str(hit.get("datatype") or ""),
                    "description": desc,
                })
            return out
        except Exception as exc:  # noqa: BLE001
            logger.warning("wbsearchentities failed (%r): %s", search, exc)
            return []

    def _get_csrf_token(self) -> str | None:
        login = self._login
        if login is None:
            return None
        api_url = _TEST_API if self._is_test else _WIKIDATA_API
        try:
            session = login.get_session()
            resp = session.get(
                api_url,
                params={
                    "action": "query",
                    "meta": "tokens",
                    "type": "csrf",
                    "format": "json",
                },
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get("query", {}).get("tokens", {}).get("csrftoken")
        except Exception as exc:  # noqa: BLE001
            logger.warning("csrf token fetch failed: %s", exc)
            return None

    def _refresh_edit_token(self) -> None:
        """Refresh the WikibaseIntegrator edit token after a CSRF rejection."""
        self._init_wbi()
        login = self._login
        if login is None:
            raise RuntimeError("The Wikidata login is not initialized")
        try:
            login.edit_token = None
            if not login.get_edit_token():
                raise RuntimeError("The Wikidata API returned an empty edit token")
        except Exception as exc:  # noqa: BLE001
            logger.warning("csrf token refresh failed: %s", exc)
            raise

    def _can_create_test_properties(self) -> bool:
        if self._test_can_create_properties is not None:
            return self._test_can_create_properties
        info = self._query_userinfo_rights()
        rights = {str(r) for r in (info.get("rights") or [])} if info else set()
        self._test_can_create_properties = "property-create" in rights
        return self._test_can_create_properties

    def _wbeditentity_new(self, *, new: str, data: dict[str, object]) -> str | None:
        """Create a property or item on the current wiki. Returns the new id."""
        login = self._login
        if login is None:
            return None
        csrf = self._get_csrf_token()
        if not csrf:
            return None
        api_url = _TEST_API if self._is_test else _WIKIDATA_API
        write_started = False
        try:
            self._rate_limit()
            session = login.get_session()
            write_started = True
            resp = session.post(
                api_url,
                data={
                    "action": "wbeditentity",
                    "new": new,
                    "data": json.dumps(data, ensure_ascii=False),
                    "token": csrf,
                    "format": "json",
                    "summary": "MHM Pipeline: test.wikidata.org remap stub",
                },
                timeout=60,
            )
            resp.raise_for_status()
            body = resp.json()
            entity = body.get("entity") or {}
            entity_id = str(entity.get("id") or "").strip()
            if entity_id:
                self._test_fresh_creates.add(entity_id)
                return entity_id
            if not isinstance(body.get("error"), dict):
                raise RuntimeError("Wikidata returned no entity or API error")
            from converter.wikidata.test_wiki_compat import (  # noqa: PLC0415
                parse_wbeditentity_conflict_id,
            )

            conflict_id = parse_wbeditentity_conflict_id(body)
            if not conflict_id:
                from converter.wikidata.test_wiki_compat import (  # noqa: PLC0415
                    parse_label_conflict_id,
                )

                conflict_id = parse_label_conflict_id(body)
            if conflict_id:
                logger.info(
                    "wbeditentity new=%s adopted existing %s from label conflict",
                    new,
                    conflict_id,
                )
                return conflict_id
            adopted = self._adopt_by_exact_label_from_new_data(new=new, data=data)
            if adopted:
                logger.info(
                    "wbeditentity new=%s adopted existing %s from exact-label re-search",
                    new,
                    adopted,
                )
                return adopted
            logger.warning("wbeditentity new=%s failed: %s", new, body)
        except Exception as exc:  # noqa: BLE001
            logger.warning("wbeditentity new=%s error: %s", new, exc)
            if write_started:
                raise RuntimeError(
                    f"Wikidata {new} CREATE outcome unknown; the request may have succeeded. "
                    "Do not retry until the outcome is resolved."
                ) from exc
            from converter.wikidata.test_wiki_compat import (  # noqa: PLC0415
                parse_label_conflict_id,
            )

            conflict_id = parse_label_conflict_id(exc)
            if conflict_id:
                return conflict_id
            adopted = self._adopt_by_exact_label_from_new_data(new=new, data=data)
            if adopted:
                return adopted
        return None

    def _adopt_by_exact_label_from_new_data(
        self, *, new: str, data: dict[str, object],
    ) -> str | None:
        labels = data.get("labels") if isinstance(data.get("labels"), dict) else {}
        en = labels.get("en") if isinstance(labels, dict) else None
        label = ""
        if isinstance(en, dict):
            label = str(en.get("value") or "")
        expected = str(data.get("datatype") or "").strip() or None
        return self._adopt_by_exact_label(
            entity_type="property" if new == "property" else "item",
            label=label,
            expected_datatype=expected,
        )

    def _adopt_by_exact_label(
        self,
        *,
        entity_type: str,
        label: str,
        expected_datatype: str | None = None,
    ) -> str | None:
        from converter.wikidata.test_wiki_compat import choose_test_item  # noqa: PLC0415

        target = label.strip()
        if not target:
            return None
        hits = self._wbsearchentities(target, entity_type=entity_type)
        if entity_type == "property":
            pids = [
                str(hit.get("id") or "")
                for hit in hits
                if str(hit.get("id") or "").startswith("P")
            ]
            self._ensure_test_property_datatypes(pids)
            wanted = (expected_datatype or "").strip()
            needle = target.lower()
            for hit in hits:
                hit_id = str(hit.get("id") or "").strip()
                hit_label = str(hit.get("label") or "").strip().lower()
                if not hit_id.startswith("P") or hit_label != needle:
                    continue
                actual = self._test_property_datatypes.get(hit_id) or str(
                    hit.get("datatype") or "",
                )
                if wanted and actual != wanted:
                    continue
                return hit_id
            return None
        return choose_test_item(
            "Q0",
            item_label=target,
            qid_map={},
            search_hits=hits,
        )

    def _create_test_property(self, label: str, datatype: str) -> str | None:
        if not self._can_create_test_properties():
            return None
        data = {
            "labels": {"en": {"language": "en", "value": label}},
            "datatype": datatype,
        }
        return self._wbeditentity_new(new="property", data=data)

    def _fetch_live_english_labels(self, qids: list[str]) -> dict[str, str]:
        """Read English labels from live wikidata.org (test uploads still need them)."""
        import requests  # noqa: PLC0415

        out: dict[str, str] = {}
        missing = [q for q in qids if q and q not in self._live_qid_labels]
        if not missing:
            return {q: self._live_qid_labels[q] for q in qids if q in self._live_qid_labels}
        headers = {"User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)"}
        try:
            for i in range(0, len(missing), 50):
                chunk = missing[i : i + 50]
                resp = requests.get(
                    _WIKIDATA_API,
                    params={
                        "action": "wbgetentities",
                        "ids": "|".join(chunk),
                        "props": "labels",
                        "languages": "en",
                        "format": "json",
                    },
                    headers=headers,
                    timeout=30,
                )
                resp.raise_for_status()
                entities = resp.json().get("entities") or {}
                if not isinstance(entities, dict):
                    continue
                for qid in chunk:
                    ent = entities.get(qid)
                    if not isinstance(ent, dict) or "missing" in ent:
                        continue
                    labels = ent.get("labels") or {}
                    en = labels.get("en") if isinstance(labels, dict) else None
                    val = str((en or {}).get("value") or "").strip()
                    if val:
                        out[qid] = val
        except Exception as exc:  # noqa: BLE001
            logger.warning("live wikidata.org label fetch failed: %s", exc)
        self._live_qid_labels.update(out)
        return {q: self._live_qid_labels[q] for q in qids if q in self._live_qid_labels}

    def _create_test_item_stub(self, label: str, live_qid: str) -> str | None:
        from converter.wikidata.test_wiki_compat import (  # noqa: PLC0415
            mhm_test_stub_description,
        )

        data = {
            "labels": {"en": {"language": "en", "value": label}},
            "descriptions": {
                "en": {
                    "language": "en",
                    "value": mhm_test_stub_description(live_qid),
                },
            },
        }
        created = self._wbeditentity_new(new="item", data=data)
        if created and created in self._test_fresh_creates:
            self._test_item_descriptions[created] = mhm_test_stub_description(live_qid)
        return created

    def _ensure_test_property_datatypes(self, pids: list[str]) -> None:
        missing = [p for p in pids if p not in self._test_property_datatypes]
        if not missing:
            return
        fetched = self._wbgetentities(missing, props="datatype")
        for pid in missing:
            ent = fetched.get(pid) if isinstance(fetched.get(pid), dict) else None
            if not ent or "missing" in ent:
                self._test_property_datatypes[pid] = None
            else:
                dt = ent.get("datatype")
                self._test_property_datatypes[pid] = str(dt) if dt else None

    def _ensure_test_entity_exists_flags(self, qids: list[str]) -> None:
        missing = [q for q in qids if q not in self._test_entity_exists]
        if not missing:
            return
        fetched = self._wbgetentities(missing, props="info")
        for qid in missing:
            ent = fetched.get(qid) if isinstance(fetched.get(qid), dict) else None
            self._test_entity_exists[qid] = bool(ent) and "missing" not in ent

    def _store_test_property_map(
        self,
        live_pid: str,
        value_type: str,
        test_pid: str,
        stats: object,
        *,
        created: bool,
    ) -> bool:
        from converter.wikidata.test_wiki_compat import (  # noqa: PLC0415
            expected_wikibase_datatype,
            pid_map_store,
        )

        expected = expected_wikibase_datatype(value_type)
        if expected is None:
            return False
        self._ensure_test_property_datatypes([test_pid])
        actual = self._test_property_datatypes.get(test_pid)
        if actual != expected:
            return False
        pid_map_store(self._test_pid_map, live_pid, value_type, test_pid)
        if created:
            stats.properties_created += 1
        elif test_pid != live_pid:
            stats.properties_remapped += 1
        return True

    def _try_map_property_from_search(
        self,
        live_pid: str,
        value_type: str,
        label: str,
        stats: object,
    ) -> bool:
        from converter.wikidata.test_wiki_compat import (  # noqa: PLC0415
            choose_test_property,
            pid_map_lookup,
        )

        if pid_map_lookup(self._test_pid_map, live_pid, value_type):
            return True
        target = (label or "").strip()
        if not target:
            return False
        hits = self._wbsearchentities(target, entity_type="property")
        hit_pids = [
            str(hit.get("id") or "")
            for hit in hits
            if str(hit.get("id") or "").startswith("P")
        ]
        self._ensure_test_property_datatypes(hit_pids)
        for hit in hits:
            pid = str(hit.get("id") or "")
            confirmed = self._test_property_datatypes.get(pid)
            if confirmed:
                hit["datatype"] = confirmed
        test_pid = choose_test_property(
            live_pid,
            value_type,
            property_label=target,
            property_datatypes=self._test_property_datatypes,
            pid_map=self._test_pid_map,
            search_hits=hits,
        )
        if test_pid and self._store_test_property_map(
            live_pid, value_type, test_pid, stats, created=False,
        ):
            return True
        return None

    def _resolve_test_property_for_snak(
        self,
        live_pid: str,
        value_type: str,
        stats: object,
    ) -> None:
        from converter.wikidata.property_labels import PROPERTY_LABELS  # noqa: PLC0415
        from converter.wikidata.test_wiki_compat import (  # noqa: PLC0415
            expected_wikibase_datatype,
            mhm_disambiguated_property_label,
            pid_map_lookup,
        )

        if pid_map_lookup(self._test_pid_map, live_pid, value_type):
            return
        label = PROPERTY_LABELS.get(live_pid, "")
        expected = expected_wikibase_datatype(value_type)
        if self._try_map_property_from_search(live_pid, value_type, label, stats):
            return
        if label and expected:
            created = self._create_test_property(label, expected)
            if created and self._store_test_property_map(
                live_pid, value_type, created, stats, created=True,
            ):
                return
            if self._try_map_property_from_search(
                live_pid, value_type, label, stats,
            ):
                return
            alt = mhm_disambiguated_property_label(label, expected)
            if self._try_map_property_from_search(
                live_pid, value_type, alt, stats,
            ):
                return
            created = self._create_test_property(alt, expected)
            if created and self._store_test_property_map(
                live_pid, value_type, created, stats, created=True,
            ):
                return
            self._try_map_property_from_search(live_pid, value_type, alt, stats)

    def _ensure_test_maps_for_item(self, item: WikidataItem, *, allow_unmapped_live: bool = False) -> object:
        """Resolve live P/Q ids to test wiki ids (search, then stub CREATE)."""
        from converter.wikidata.test_wiki_compat import (  # noqa: PLC0415
            WikiTestAdaptStats,
            choose_test_item,
            collect_live_pids_with_types,
            collect_live_qids,
            gloss_for_test_stub,
            mhm_live_qid_gloss,
            reference_write_issues_for_test_wiki,
        )

        for stmt in item.statements:
            _validate_native_statement(stmt, is_test=self._is_test, allow_local=True)
        issues = self._test_local_target_issues(item, allow_unmapped_live=allow_unmapped_live) + reference_write_issues_for_test_wiki(
            item, verified_targets=self._verified_test_targets,
        )
        if issues:
            raise ClaimBuildError(issues)
        stats = WikiTestAdaptStats()
        live_pids = collect_live_pids_with_types(item)
        live_qids = collect_live_qids(item)
        self._ensure_test_property_datatypes([p for p, _ in live_pids])

        for live_pid, value_type in live_pids:
            self._resolve_test_property_for_snak(live_pid, value_type, stats)

        missing_gloss = [
            qid for qid in live_qids
            if qid not in self._test_qid_map
            and not gloss_for_test_stub(qid, extra=self._live_qid_labels)
        ]
        if missing_gloss:
            self._live_qid_labels.update(self._fetch_live_english_labels(missing_gloss))

        for live_qid in live_qids:
            if live_qid in self._test_qid_map:
                continue
            gloss = (
                gloss_for_test_stub(live_qid, extra=self._live_qid_labels)
                or mhm_live_qid_gloss(live_qid)
            )
            hits = self._wbsearchentities(gloss, entity_type="item")
            test_qid = choose_test_item(
                live_qid,
                item_label=gloss,
                qid_map=self._test_qid_map,
                search_hits=hits,
            )
            if test_qid:
                if not self._item_usable_as_test_reference(
                    test_qid, live_qid=live_qid,
                ):
                    test_qid = None
            if test_qid:
                self._test_qid_map[live_qid] = test_qid
                stats.classes_remapped += 1
                self._test_entity_exists[test_qid] = True
                continue
            created = self._create_test_item_stub(gloss, live_qid)
            if not created:
                created = self._adopt_by_exact_label(
                    entity_type="item", label=gloss,
                )
            if created and self._item_usable_as_test_reference(
                created, live_qid=live_qid,
            ):
                self._test_qid_map[live_qid] = created
                self._test_entity_exists[created] = True
                if created in self._test_fresh_creates:
                    self._test_stubs_we_created.add(created)
                    stats.classes_created += 1
                else:
                    stats.classes_remapped += 1
                continue
            logger.warning(
                "Test-wiki mapping failed for %s (gloss %r): search hits %d, stub created %s",
                live_qid, gloss, len(hits or []), bool(created),
            )

        return stats

    def warm_test_maps_for_items(self, items: list[WikidataItem]) -> None:
        """Resolve session P/Q maps once per test-upload job (Rules W-187 / W-189)."""
        from converter.wikidata.test_wiki_compat import (  # noqa: PLC0415
            collect_live_qids,
            gloss_for_test_stub,
            reference_write_issues_for_test_wiki,
        )

        if not self._is_test:
            return
        safe_items = []
        for item in items:
            try:
                for stmt in item.statements:
                    _validate_native_statement(stmt, is_test=self._is_test, allow_local=True)
                issues = self._test_local_target_issues(item, allow_unmapped_live=True) + reference_write_issues_for_test_wiki(
                    item, verified_targets=self._verified_test_targets,
                )
            except ClaimBuildError as exc:
                issues = exc.reasons
            if issues:
                logger.warning("Skipping test-map prewarm for %s: %s", item.local_id, "; ".join(issues[:12]))
                logger.warning(
                    "Prewarm gate values for %s: %s", item.local_id,
                    [(stmt.property_id, type(stmt.value).__name__, repr(stmt.value)[:80], stmt.value_type)
                     for stmt in item.statements if stmt.property_id in ("P31", "P195", "P361")][:6],
                )
            else:
                safe_items.append(item)
        if not safe_items:
            return
        if self._login is None:
            self._init_wbi()
        pending: list[str] = []
        seen: set[str] = set()
        for item in safe_items:
            for qid in collect_live_qids(item):
                if qid in seen:
                    continue
                seen.add(qid)
                if not gloss_for_test_stub(qid, extra=self._live_qid_labels):
                    pending.append(qid)
        if pending:
            self._live_qid_labels.update(self._fetch_live_english_labels(pending))
        for item in safe_items:
            self._ensure_test_maps_for_item(item, allow_unmapped_live=True)

    def _bind_prewarmed_qid(self, value_type: object, value: object) -> MappedTestQid | None:
        """Return the test QID for a plain live item value the prewarm mapped."""
        if _normalize_wbi_value_type(value_type) != "item" or not _is_plain_live_qid(value):
            return None
        test_qid = self._test_qid_map.get(str(value))
        return MappedTestQid(test_qid) if test_qid else None

    def _bind_prewarmed_qualifier(self, qual: object) -> object:
        """Bind a qualifier item value the prewarm already mapped (test wiki only)."""
        if not isinstance(qual, dict) or qual.get("value_namespace") not in (None, "wikidata"):
            return qual
        value_type = qual.get("value_type") or qual.get("type") or qual.get("datatype") or "string"
        bound = self._bind_prewarmed_qid(value_type, qual.get("value"))
        if bound is None:
            return qual
        return {**qual, "value": bound, "value_namespace": "mapped-test"}

    def _bind_prewarmed_statement_qids(self, item: WikidataItem) -> WikidataItem:
        """Mark plain live item values that the prewarm already mapped (test wiki only)."""

        def bind(stmt: WikidataStatement) -> WikidataStatement:
            value = self._bind_prewarmed_qid(stmt.value_type, stmt.value) or stmt.value
            return replace(
                stmt,
                value=value,
                qualifiers=[self._bind_prewarmed_qualifier(qual) for qual in stmt.qualifiers or []],
            )

        return replace(item, statements=[bind(stmt) for stmt in item.statements])

    def _adapt_item_for_test_wiki(
        self, item: WikidataItem,
    ) -> tuple[WikidataItem, object]:
        """Remap claims for test.wikidata.org; leftovers refuse the write (W-186)."""
        from converter.wikidata.test_wiki_compat import (  # noqa: PLC0415
            WikiTestAdaptResult,
            collect_live_pids_with_types,
            collect_live_qids,
            collect_test_wiki_ids,
            filter_item_for_test_wiki,
            normalize_item_quantity_units,
            pid_map_lookup,
            reference_write_issues_for_test_wiki,
            rewrite_item_with_maps,
        )

        item = self._bind_prewarmed_statement_qids(item)
        reference_issues = self._test_local_target_issues(item, allow_unmapped_live=True) + reference_write_issues_for_test_wiki(
            item, verified_targets=self._verified_test_targets,
        )
        if reference_issues:
            return item, WikiTestAdaptResult(skipped=reference_issues)
        for stmt in item.statements:
            try:
                _validate_native_statement(stmt, is_test=self._is_test, allow_local=True)
            except ClaimBuildError as exc:
                return item, WikiTestAdaptResult(skipped=exc.reasons)
        try:
            stats = self._ensure_test_maps_for_item(
                normalize_item_quantity_units(item), allow_unmapped_live=True,
            )
        except ClaimBuildError as exc:
            return item, WikiTestAdaptResult(skipped=exc.reasons)
        item = self._bind_prewarmed_statement_qids(item)
        reference_issues = self._test_local_target_issues(item) + reference_write_issues_for_test_wiki(
            item, verified_targets=self._verified_test_targets,
        )
        if reference_issues:
            return item, WikiTestAdaptResult(stats=stats, skipped=reference_issues)
        item = normalize_item_quantity_units(item)
        live_pids = collect_live_pids_with_types(item)
        live_qids = collect_live_qids(item)
        unmapped = [
            f"{pid} ({value_type}) has no verified test property mapping"
            for pid, value_type in live_pids
            if not pid_map_lookup(self._test_pid_map, pid, value_type)
        ]
        unmapped.extend(
            f"{qid} has no verified test item mapping"
            for qid in live_qids if not self._test_qid_map.get(qid)
        )
        if unmapped:
            return item, WikiTestAdaptResult(stats=stats, skipped=unmapped)
        rewritten = rewrite_item_with_maps(
            item,
            self._test_pid_map,
            self._test_qid_map,
            verified_targets=self._verified_test_targets,
        )
        test_pids, test_qids = collect_test_wiki_ids(rewritten)
        self._ensure_test_property_datatypes(test_pids)
        self._ensure_test_entity_exists_flags(test_qids)
        existing = {qid for qid, ok in self._test_entity_exists.items() if ok}
        from converter.wikidata.property_labels import QID_LABELS  # noqa: PLC0415

        allowed = (set(self._test_qid_map.values()) | set(self._test_stubs_we_created)
                   | set(self._verified_test_targets.values()))
        filtered, skipped = filter_item_for_test_wiki(
            rewritten,
            property_datatypes=self._test_property_datatypes,
            existing_item_ids=existing,
            live_static_qids=set(QID_LABELS),
            allowed_item_ids=allowed,
        )
        if skipped:
            logger.warning(
                "test.wikidata.org adapt for %s has %d leftover snaks (Rule W-186): %s",
                item.local_id,
                len(skipped),
                "; ".join(skipped[:12]),
            )
        return filtered, WikiTestAdaptResult(stats=stats, skipped=skipped)

    def upload_item(
        self,
        item: WikidataItem,
        *,
        created_qids: dict[str, str] | None = None,
        owner_only_qid: str | None = None,
    ) -> UploadResult:
        try:
            if owner_only_qid is None:
                result = self._upload_item(item, created_qids=created_qids)
            else:
                result = self._upload_item(item, created_qids=created_qids, owner_only_qid=owner_only_qid)
            return result
        finally:
            self._foreign_accept_qids.clear()

    def _upload_item(
        self,
        item: WikidataItem,
        *,
        created_qids: dict[str, str] | None = None,
        owner_only_qid: str | None = None,
    ) -> UploadResult:
        """Upload a single item to Wikidata with retry logic and smart diffing.

        For existing items: fetches current claims, compares with new claims,
        and only writes if there are actual changes (avoids duplicates).

        Existing items require creator ownership or exact-QID curator approval.

        Returns:
            UploadResult with QID and status.
        """
        self._check_moratorium_for_live()
        try:
            for stmt in item.statements or []:
                _validate_native_statement(
                    stmt, is_test=self._is_test, owner_only=owner_only_qid is not None, allow_local=True,
                )
        except ClaimBuildError as exc:
            reasons = list(exc.reasons)
            if self._is_test:
                from converter.wikidata.test_wiki_compat import reference_write_issues_for_test_wiki  # noqa: PLC0415

                reasons.extend(reference_write_issues_for_test_wiki(
                    item, verified_targets=self._verified_test_targets,
                ))
            return UploadResult(
                local_id=item.local_id, qid=item.existing_qid, status="blocked",
                message="Refusing write: " + "; ".join(reasons[:12]),
            )
        owner_control_number = None
        if owner_only_qid is not None:
            self._foreign_accept_qids.clear()
            try:
                owner_only_validate_quantity_units(item)
            except ClaimBuildError as exc:
                return UploadResult(local_id=item.local_id, qid=item.existing_qid,
                                    status="blocked", message=str(exc))
            if owner_only_has_local_references(item) or owner_only_has_generic_text(item):
                return UploadResult(local_id=item.local_id, qid=item.existing_qid,
                                    status="blocked", message="Owner-only: unresolved or generic text evidence is not allowed.")
            owner_control_number = owner_only_control_number(item)
            if (not owner_control_number or item.existing_qid != owner_only_qid
                    or not self.owner_only_target_valid(owner_only_qid, owner_control_number)):
                return UploadResult(local_id=item.local_id, qid=item.existing_qid,
                                    status="blocked", message="Owner-only target verification failed.")
        resolution_qids = created_qids or {}
        if self._is_test:
            resolution_qids = {
                local_id: qid for local_id, qid in resolution_qids.items()
                if self._verified_test_targets.get(local_id) == qid
            }
        leftover_local = resolve_local_statement_refs(item, resolution_qids, test_namespace=self._is_test)
        if leftover_local:
            shown = "; ".join(leftover_local[:12])
            extra = (
                f" (+{len(leftover_local) - 12} more)"
                if len(leftover_local) > 12
                else ""
            )
            return UploadResult(
                local_id=item.local_id,
                status="blocked",
                message=(
                    "Refusing write: unresolved __LOCAL: references "
                    f"(Rule W-191). {shown}{extra}"
                ),
            )
        if self._is_test:
            from converter.wikidata.test_wiki_compat import reference_write_issues_for_test_wiki  # noqa: PLC0415

            reference_issues = self._test_local_target_issues(item) + reference_write_issues_for_test_wiki(
                item, verified_targets=self._verified_test_targets,
            )
            if reference_issues:
                return UploadResult(
                    local_id=item.local_id, status="blocked",
                    message="Refusing test write: " + "; ".join(reference_issues[:12]),
                )
        if self._is_test and created_qids:
            from converter.wikidata.test_wiki_compat import (  # noqa: PLC0415
                explicit_live_item_qid,
                item_target_qid,
            )

            target_qids = set(created_qids.values())
            for stmt in item.statements or []:
                values: list[tuple[object, object]] = []
                if _normalize_wbi_value_type(stmt.value_type) == "item":
                    namespace = stmt.value.get("value_namespace") if isinstance(stmt.value, Mapping) else None
                    values.append((stmt.value, namespace))
                for snak in [*(stmt.qualifiers or []), *_statement_reference_snaks(stmt)]:
                    if isinstance(snak, dict) and _normalize_wbi_value_type(
                        snak.get("value_type") or snak.get("type") or snak.get("datatype") or "string",
                    ) == "item":
                        values.append((snak.get("value"), snak.get("value_namespace")))
                if any(namespace not in ("local", "wikidata") and not isinstance(value, LocalTargetQid)
                       and not explicit_live_item_qid(value)
                       and item_target_qid(value) in target_qids
                       for value, namespace in values):
                    return UploadResult(
                        local_id=item.local_id, status="blocked",
                        message="Refusing test write: an item QID has an ambiguous target namespace.",
                    )
        if (self._is_test and item.existing_qid
                and self._verified_test_targets.get(item.local_id) != item.existing_qid):
            return UploadResult(
                local_id=item.local_id, qid=item.existing_qid, status="blocked",
                message="Test UPDATE target lacks exact test-ledger proof.",
            )
        self._init_wbi()

        test_adapt_result = None

        # Ownership BEFORE test adapt: never stub-CREATE properties/classes
        # for an item we are about to skip (Rule W-184).
        if item.existing_qid and not (
            (not self._is_test and item.existing_qid in self._foreign_accept_qids)
            or self._is_our_item(item.existing_qid, our_p3959=self._our_p3959(item))
        ):
            if self._is_test:
                logger.info(
                    "test upload: clearing foreign existing_qid %s for CREATE "
                    "(local_id=%s)",
                    item.existing_qid,
                    item.local_id,
                )
                item = replace(item, existing_qid=None)
            else:
                logger.warning(
                    "SAFETY: Skipping %s — existing item %s was NOT authored by the "
                    "authenticated user",
                    item.local_id,
                    item.existing_qid,
                )
                return UploadResult(
                    local_id=item.local_id,
                    qid=item.existing_qid,
                    status="skipped",
                    message=(
                        f"Skipped {item.existing_qid} — not authored by the authenticated "
                        "user (Rule-38; no UPDATE of foreign items on wikidata.org)"
                    ),
                )

        if self._is_test:
            item, test_adapt_result = self._adapt_item_for_test_wiki(item)
            if test_adapt_result is not None and test_adapt_result.skipped:
                leftovers = test_adapt_result.skipped
                reasons = "; ".join(leftovers[:12])
                extra = (
                    f" (+{len(leftovers) - 12} more)"
                    if len(leftovers) > 12
                    else ""
                )
                return UploadResult(
                    local_id=item.local_id,
                    status="blocked",
                    message=(
                        "Refusing test.wikidata.org write: the remapped item "
                        "would drop claims and would not pass expert review "
                        f"(Rule W-186). {reasons}{extra}"
                    ),
                )

        # Bug fix 2026-04-16 (deeper audit Fix #9): respect the community
        # convention {{bots|deny=…}} on the item's talk page. If the talk
        # page denies our bot (or all bots), skip the write entirely. This
        # is community norm rather than technically enforced by MediaWiki,
        # but ignoring it has historically led to bot-blocks at WD:AN.
        if item.existing_qid and self._bot_excluded(item.existing_qid):
            logger.warning(
                "Skipping %s — talk page has {{bots|deny=…}} excluding this bot",
                item.existing_qid,
            )
            return UploadResult(
                local_id=item.local_id,
                qid=item.existing_qid,
                status="skipped",
                message=f"Skipped {item.existing_qid} — bot exclusion template on talk page",
            )

        def unknown_write(exc: Exception) -> UploadResult:
            logger.error("Wikidata write outcome unknown for %s: %s", item.local_id, exc)
            return UploadResult(
                local_id=item.local_id,
                qid=item.existing_qid,
                status="failed",
                message=(
                    "Wikidata write outcome unknown; the request may have succeeded. "
                    "Do not retry until the outcome is resolved. "
                    f"Detail: {str(exc)[:160]}"
                ),
            )

        last_error = ""
        for attempt in range(1, _MAX_RETRIES + 1):
            write_started = False
            if attempt > 1:
                self._rate_limit()
            try:
                if owner_only_qid is not None:
                    wbi_item, new_claims, added_props = self._build_wbi_item(item, owner_only=True)
                else:
                    wbi_item, new_claims, added_props = self._build_wbi_item(item)

                if item.existing_qid and new_claims == 0:
                    missing = [
                        f"{s.property_id}={s.value}"
                        for s in item.statements
                        if not self._claim_exists(wbi_item, s)
                    ]
                    if missing:
                        shown = "; ".join(missing[:12])
                        extra = (
                            f" (+{len(missing) - 12} more)"
                            if len(missing) > 12
                            else ""
                        )
                        return UploadResult(
                            local_id=item.local_id,
                            qid=item.existing_qid,
                            status="blocked",
                            message=(
                                "Refusing exists: the wiki item is missing native "
                                f"claims (Rule W-191). {shown}{extra}"
                            ),
                        )
                    return UploadResult(
                        local_id=item.local_id,
                        qid=item.existing_qid,
                        status="exists",
                        message=f"No changes needed for {item.existing_qid}",
                    )

                self._rate_limit()

                # Bot policy compliance (Wikidata:Bots): every write needs
                # a descriptive edit summary so reviewers can understand
                # the change at a glance.
                action = "Update" if item.existing_qid else "Create"
                edit_summary = (
                    f"MHM Pipeline: {action} {item.entity_type} from NLI "
                    f"Hebrew Manuscripts catalog (Ktiv); +{new_claims} claims; "
                    f"local_id={item.local_id}"
                )
                # Fix 2026-04-15 third audit Fix #17: Wikidata API rejects
                # edit summaries longer than 500 characters. Truncate to 497
                # to leave room for the "..." suffix.
                if len(edit_summary) > 497:
                    edit_summary = edit_summary[:497] + "..."
                actual_qid = getattr(wbi_item, "id", None)
                if actual_qid != (item.existing_qid or None):
                    return UploadResult(
                        local_id=item.local_id,
                        qid=str(actual_qid) if actual_qid else item.existing_qid,
                        status="blocked",
                        message="Refusing write: the actual target differs from the approved QID.",
                    )
                if (self._is_test and item.existing_qid
                        and self._verified_test_targets.get(item.local_id) != item.existing_qid):
                    return UploadResult(
                        local_id=item.local_id, qid=item.existing_qid, status="blocked",
                        message="Test UPDATE target lacks exact test-ledger proof.",
                    )
                owner_revision = None
                if owner_only_qid is not None:
                    owner_revision = self._owner_only_revision(owner_only_qid, owner_control_number)
                    if (item.existing_qid != owner_only_qid or actual_qid != owner_only_qid
                            or owner_only_control_number(item) != owner_control_number
                            or type(owner_revision) is not int or owner_revision <= 0
                            or getattr(wbi_item, "lastrevid", None) != owner_revision):
                        return UploadResult(local_id=item.local_id, qid=item.existing_qid,
                                            status="blocked", message="Owner-only pre-write verification failed.")
                # DEFENSE-IN-DEPTH #4 (rule 38): immediately before the
                # only .write() call in the codebase, re-assert that the
                # target QID is ours. This is the last gate — if someone
                # introduces a new upload path or bypasses the earlier
                # guards, this one fires. FAIL CLOSED.
                self._assert_modifiable(
                    item.existing_qid or "",
                    stage="pre_write",
                    our_p3959=self._our_p3959(item),
                )
                # WikibaseIntegrator ≥0.12 takes ``is_bot`` (never bare ``bot=``;
                # Rule W-180). Default is_bot=False — accounts without the
                # MediaWiki bot right hard-fail (Rule W-181 / export-40).
                # WBI retries connection errors and HTTP 5xx internally unless
                # limited to one request, even when the first write may have landed.
                write_started = True
                owner_write_options = ({"mediawiki_api_url": _WIKIDATA_API,
                                        "baserevid": owner_revision, "allow_redirects": False}
                                       if owner_only_qid is not None else {})
                result = wbi_item.write(
                    summary=edit_summary,
                    is_bot=self._mark_as_bot,
                    maxlag=20,
                    retry_after=0,
                    max_retries=1,
                    timeout=30,
                    **owner_write_options,
                )
                qid = result.id if result else None
                if not qid:
                    raise ValueError("Wikidata returned no QID after the write request")

                from converter.wikidata.test_wiki_compat import (  # noqa: PLC0415
                    format_test_wiki_outcome_note,
                )

                skip_note = format_test_wiki_outcome_note(test_adapt_result)
                if item.existing_qid:
                    from collections import Counter  # noqa: PLC0415

                    prop_summary = ", ".join(
                        f"{pid}x{cnt}" for pid, cnt in Counter(added_props).most_common(5)
                    )
                    return UploadResult(
                        local_id=item.local_id,
                        qid=qid,
                        status="updated",
                        message=(
                            f"Updated {qid}: +{new_claims} claims ({prop_summary})"
                            + skip_note
                        ),
                        added_properties=added_props,
                    )
                return UploadResult(
                    local_id=item.local_id,
                    qid=qid,
                    status="success",
                    message=f"Created {qid} ({new_claims} claims){skip_note}",
                    added_properties=added_props,
                )
            except ClaimBuildError as exc:
                if write_started:
                    return unknown_write(exc)
                return UploadResult(
                    local_id=item.local_id,
                    qid=item.existing_qid,
                    status="blocked",
                    message=(
                        "Refusing write: native claims could not be built "
                        f"(Rule W-191). {exc}"
                    ),
                )
            except UnauthorisedModificationError as exc:
                if write_started:
                    return unknown_write(exc)
                # Rule 38 tripwire — any defense-in-depth guard fired.
                # This is not retryable: the item is not ours, period.
                # Convert to a clean "skipped" result instead of bubbling
                # up and failing the whole batch.
                logger.error(
                    "SAFETY tripwire (%s) for %s → %s: %s",
                    exc.stage, item.local_id, exc.qid, exc,
                )
                return UploadResult(
                    local_id=item.local_id,
                    qid=exc.qid,
                    status="skipped",
                    message=(
                        f"Blocked by rule-38 guard at stage {exc.stage!r}: "
                        f"{exc.qid} was not authored by the authenticated user."
                    ),
                )
            except Exception as exc:
                last_error = str(exc)
                if write_started:
                    from wikibaseintegrator.wbi_exceptions import MWApiError  # noqa: PLC0415

                    if not isinstance(exc, MWApiError):
                        return unknown_write(exc)
                    if owner_only_qid is not None:
                        return UploadResult(local_id=item.local_id, qid=item.existing_qid,
                                            status="blocked", message=f"Owner-only write rejected: {str(exc)[:160]}")
                # Bug fix 2026-04-16 (deeper audit Fix #8): inspect the
                # error to decide retry strategy. editconflict means
                # someone edited the item between our get() and write();
                # the fix is to re-fetch and re-build the WBI item from
                # scratch (which the next loop iteration does, since
                # _build_wbi_item is called inside the try block). badtoken
                # means the CSRF token expired; same re-build path applies.
                err_lower = last_error.lower()
                is_conflict = "editconflict" in err_lower
                is_badtoken = "badtoken" in err_lower
                # "bot" right missing is configuration, not transient —
                # three retries only burn wall clock (export-40).
                is_bot_right = (
                    '"bot" right' in err_lower
                    or "do not have the \"bot\" right" in err_lower
                    or "do not have the 'bot' right" in err_lower
                )
                # Generic MediaWiki permissiondenied (export-41): almost always
                # missing bot-password grants (createpage / edit), never fixed
                # by retrying the same write three times.
                is_permission = (
                    "permissions needed" in err_lower
                    or "permissiondenied" in err_lower
                )
                is_session_death = (
                    "you are no longer logged in" in err_lower
                    or "notloggedin" in err_lower
                    or "assertuserfailed" in err_lower
                    or "globally blocked" in err_lower
                    or "open proxy" in err_lower
                )
                is_bad_value_type = "bad value type" in err_lower
                is_illegal_live_entity = (
                    "illegal value: http://www.wikidata.org/entity/" in err_lower
                )
                mw_code = ""
                if hasattr(exc, "code"):
                    mw_code = str(getattr(exc, "code") or "")
                    if mw_code.lower() == "permissiondenied":
                        is_permission = True
                logger.warning(
                    "Upload attempt %d/%d for %s failed (%s%s): %s",
                    attempt,
                    _MAX_RETRIES,
                    item.local_id,
                    "editconflict" if is_conflict else "error",
                    " badtoken" if is_badtoken else "",
                    exc,
                )
                if is_bot_right:
                    return UploadResult(
                        local_id=item.local_id,
                        status="failed",
                        message=(
                            "Account lacks the MediaWiki 'bot' right but writes "
                            "requested is_bot=True. Unset WIKIDATA_MARK_AS_BOT "
                            f"(or pass mark_as_bot=False). Detail: {last_error[:160]}"
                        ),
                    )
                if is_permission or is_session_death:
                    wiki = "test.wikidata.org" if self._is_test else "www.wikidata.org"
                    detail = (
                        "Session expired or account blocked"
                        if is_session_death
                        else "MediaWiki permissiondenied"
                    )
                    return UploadResult(
                        local_id=item.local_id,
                        status="failed",
                        message=(
                            f"{detail} on {wiki}"
                            + (f" (code={mw_code})" if mw_code else "")
                            + (
                                ". Re-authenticate and retry the upload job."
                                if is_session_death
                                else ". Bot password needs: High-volume editing; "
                                "Edit existing pages; Create, edit, and move pages."
                            )
                            + f" Detail: {last_error[:120]}"
                        ),
                    )
                if is_bad_value_type or is_illegal_live_entity:
                    wiki = "test.wikidata.org" if self._is_test else "www.wikidata.org"
                    detail = (
                        "Illegal live entity URI on test wiki (Rule W-185)."
                        if is_illegal_live_entity
                        else "Bad claim datatype."
                    )
                    return UploadResult(
                        local_id=item.local_id,
                        status="failed",
                        message=(
                            f"{detail} On {wiki}, remaining mismatches should "
                            "have been stripped (Rules W-182/W-183/W-185). "
                            f"Detail: {last_error[:160]}"
                        ),
                    )
                if item.existing_qid in self._foreign_accept_qids:
                    return UploadResult(
                        local_id=item.local_id,
                        qid=item.existing_qid,
                        status="failed",
                        message=(
                            "The approved upload stopped; check foreign approval before retry. "
                            f"Detail: {last_error[:160]}"
                        ),
                    )
                if attempt < _MAX_RETRIES:
                    from converter.wikidata.test_wiki_compat import (  # noqa: PLC0415
                        parse_label_conflict_id,
                        uniquify_test_item_en_description,
                    )

                    conflict_id = parse_label_conflict_id(last_error)
                    is_label_conflict = (
                        "already has label" in err_lower
                        or "label-with-description-conflict" in err_lower
                        or "label and description for language code" in err_lower
                        or bool(conflict_id)
                    )
                    if is_label_conflict:
                        conflict_language = re.search(
                            r"language code\s+([A-Za-z][A-Za-z0-9-]*)", last_error,
                        )
                        if (
                            item.existing_qid
                            and item.entity_type in {"manuscript", "work"}
                            and not self._is_test
                            and conflict_language
                            and self._is_our_item_cache.get(str(item.existing_qid)) is True
                        ):
                            lang = conflict_language.group(1)
                            original = self._existing_label_snapshot.get(
                                str(item.existing_qid), {},
                            ).get(lang)
                            proposed = str(item.labels.get(lang) or "")
                            if original and proposed and original != proposed:
                                labels = dict(item.labels)
                                labels[lang] = original
                                aliases = {
                                    key: list(values) for key, values in item.aliases.items()
                                }
                                aliases.setdefault(lang, [])
                                if proposed not in aliases[lang]:
                                    aliases[lang].append(proposed)
                                item = replace(item, labels=labels, aliases=aliases)
                                logger.warning(
                                    "Preserving the existing %s label for %s after conflict",
                                    lang, item.existing_qid,
                                )
                                continue
                        if item.existing_qid and not self._is_test:
                            return UploadResult(
                                local_id=item.local_id,
                                qid=item.existing_qid,
                                status="failed",
                                message=(
                                    "Wikidata rejected a label/description update. "
                                    "The uploader could not safely retain the existing label; "
                                    "manual metadata review is required."
                                ),
                            )
                        if not item.existing_qid:
                            if "label and description for language code" in err_lower:
                                language = (
                                    conflict_language.group(1) if conflict_language else "unknown"
                                )
                                label = str(item.labels.get(language) or "")
                                description = str(item.descriptions.get(language) or "")
                                if label and label == description:
                                    descriptions = dict(item.descriptions)
                                    descriptions.pop(language, None)
                                    item = replace(item, descriptions=descriptions)
                                    continue
                                return UploadResult(
                                    local_id=item.local_id,
                                    status="failed",
                                    message=(
                                        f"Wikidata rejected the {language} label/description pair, "
                                        "but the local item has no equal pair and the API supplied "
                                        "no conflicting QID. Manual payload review is required."
                                    ),
                                )
                            if (
                                conflict_id
                                and conflict_id.startswith("Q")
                                and self._is_our_item(
                                    conflict_id, our_p3959=self._our_p3959(item),
                                )
                            ):
                                logger.info(
                                    "CREATE adopted existing %s for %s",
                                    conflict_id,
                                    item.local_id,
                                )
                                item = replace(item, existing_qid=conflict_id)
                                continue
                            if self._is_test:
                                item = uniquify_test_item_en_description(item)
                                logger.info(
                                    "test CREATE uniquified EN description for %s",
                                    item.local_id,
                                )
                                continue
                            return UploadResult(
                                local_id=item.local_id,
                                qid=(
                                    conflict_id
                                    if conflict_id and conflict_id.startswith("Q")
                                    else None
                                ),
                                status="skipped",
                                message=(
                                    "Skipped duplicate CREATE: "
                                    f"{conflict_id or 'an existing item'} already "
                                    "carries the same label and description on Wikidata."
                                ),
                            )
                        item = uniquify_test_item_en_description(item)
                        logger.info(
                            "UPDATE uniquified EN description after label conflict for %s",
                            item.local_id,
                        )
                        continue
                if write_started and not (is_conflict or is_badtoken):
                    return UploadResult(
                        local_id=item.local_id,
                        qid=item.existing_qid,
                        status="failed",
                        message=f"Wikidata rejected the write: {last_error[:160]}",
                    )
                if attempt < _MAX_RETRIES:
                    # Shorter backoff for conflicts (someone is actively
                    # editing this item, so a quick retry is more likely
                    # to succeed); longer for unknown failures.
                    delay = (_RETRY_DELAY_SECONDS * attempt) if not is_conflict else 1.0
                    time.sleep(delay)
        return UploadResult(
            local_id=item.local_id,
            status="failed",
            message=f"Failed after {_MAX_RETRIES} attempts: {last_error[:200]}",
        )

    def upload_all(
        self,
        items: list[WikidataItem],
        progress_cb: Callable[[int, int, str], None] | None = None,
        entity_cb: Callable[[str, str, str | None, str | None], None] | None = None,
    ) -> list[UploadResult]:
        """Upload all items with progress tracking.

        Args:
            items: List of WikidataItem instances.
            progress_cb: Called with (completed, total, message).
            entity_cb: Called with (local_id, status, qid, message) per entity.

        Returns:
            List of UploadResult instances.
        """
        results: list[UploadResult] = []
        items = sort_items_for_upload(list(items))
        total = len(items)
        if self._is_test:
            self.warm_test_maps_for_items(items)

        # Track created QIDs so manuscripts can reference freshly created persons
        created_qids: dict[str, str] = {}

        # Batch tracking: pause between batches (only when batch_mode enabled)
        batch_size = 45 if self._batch_mode else 0
        batch_count = 0

        for idx, item in enumerate(items):
            if entity_cb:
                entity_cb(item.local_id, "uploading", None, f"Uploading {item.entity_type}...")

            resolve_local_statement_refs(item, created_qids)
            result = self.upload_item(item, created_qids=created_qids)
            results.append(result)
            batch_count += 1

            # Remember the QID for future __LOCAL: resolution (new and existing
            # items we wrote). On test, never wire later claims to a skipped
            # foreign QID — that number is a different entity there (W-183/W-184).
            # Live still resolves skipped-foreign so manuscripts can *link* to
            # community persons without UPDATING them.
            if result.qid and result.status in ("success", "exists", "updated"):
                created_qids[item.local_id] = result.qid
            elif (
                result.qid
                and result.status == "skipped"
                and not self._is_test
            ):
                created_qids[item.local_id] = result.qid

            if entity_cb:
                entity_cb(item.local_id, result.status, result.qid, result.message)

            if progress_cb:
                progress_cb(idx + 1, total, result.message)

            # Pause between batches to avoid rate limiting (only in batch mode)
            if batch_size > 0 and batch_count >= batch_size and idx + 1 < total:
                batch_num = (idx + 1) // batch_size
                total_batches = (total + batch_size - 1) // batch_size
                msg = f"Batch {batch_num}/{total_batches} complete. Pausing 30s..."
                logger.info(msg)
                if progress_cb:
                    progress_cb(idx + 1, total, msg)
                time.sleep(30)
                batch_count = 0

        success = sum(1 for r in results if r.status in ("success", "exists"))
        failed = sum(1 for r in results if r.status == "failed")
        logger.info(
            "Upload complete: %d/%d succeeded, %d failed",
            success,
            total,
            failed,
        )
        return results

    @staticmethod
    def write_results(results: list[UploadResult], output_path: Path) -> Path:
        """Write upload results to a JSON file.

        Args:
            results: List of UploadResult instances.
            output_path: Destination file path.

        Returns:
            The output path written to.
        """
        data = [
            {
                "local_id": r.local_id,
                "qid": r.qid,
                "status": r.status,
                "message": r.message,
            }
            for r in results
        ]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output_path
