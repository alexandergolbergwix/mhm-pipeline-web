"""Fetch live Wikidata entities and diff them against Studio-built items."""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_QID_RE = re.compile(r"^Q\d+$", re.IGNORECASE)

CompareStatus = Literal["same", "conflict", "wikidata_only", "studio_only"]
CompareKind = Literal["label", "description", "statement"]
ChoiceSource = Literal["wikidata", "studio"]


class CompareFieldRow(BaseModel):
    kind: CompareKind
    key: str
    label: str
    wikidata_value: str | None = None
    studio_value: str | None = None
    status: CompareStatus
    studio_statement_index: int | None = None


class WikidataEntitySummary(BaseModel):
    qid: str
    labels: dict[str, str] = Field(default_factory=dict)
    descriptions: dict[str, str] = Field(default_factory=dict)
    statement_count: int = 0


class WikidataCompareResponse(BaseModel):
    qid: str
    studio_local_id: str
    wikidata: WikidataEntitySummary
    studio_label: str
    rows: list[CompareFieldRow]
    has_conflicts: bool
    conflict_count: int


class ApplyCompareChoicesRequest(BaseModel):
    policy: Literal["wikidata", "studio", "custom"] = "custom"
    choices: list[dict[str, Any]] = Field(default_factory=list)


class ApplyCompareChoicesResponse(BaseModel):
    labels: dict[str, str | None]
    descriptions: dict[str, str | None]
    remove_statements: list[int]
    add_statements: list[dict[str, Any]]
    statement_edits: dict[str, dict[str, Any]]


async def fetch_wikidata_entity(qid: str) -> dict[str, Any]:
    """Return raw ``wbgetentities`` payload for one QID."""
    if not _QID_RE.match(qid.strip()):
        raise ValueError(f"invalid QID {qid!r}")
    params = {
        "action": "wbgetentities",
        "format": "json",
        "ids": qid.strip().upper(),
        "props": "labels|descriptions|aliases|claims",
        "languages": "en|he",
        "languagefallback": 1,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            _WIKIDATA_API,
            params=params,
            headers={"User-Agent": "mhm-pipeline-web/1.0 (wikidata-compare)"},
        )
        r.raise_for_status()
        data = r.json()
    entities = data.get("entities") or {}
    ent = entities.get(qid.strip().upper())
    if not ent or ent.get("missing"):
        raise LookupError(f"Wikidata entity {qid} not found")
    return ent


def _lang_map(section: dict[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for lang, obj in (section or {}).items():
        if isinstance(obj, dict) and obj.get("value"):
            out[lang] = str(obj["value"])
    return out


def _format_datavalue(dv: dict[str, Any]) -> str:
    dtype = dv.get("type", "")
    val = dv.get("value")
    if dtype == "wikibase-entityid" and isinstance(val, dict):
        return str(val.get("id") or "")
    if dtype == "monolingualtext" and isinstance(val, dict):
        return str(val.get("text") or "")
    if dtype == "quantity" and isinstance(val, dict):
        amount = val.get("amount", "")
        unit = val.get("unit", "")
        if isinstance(unit, str) and unit.startswith("http"):
            unit = unit.rsplit("/", 1)[-1]
        return f"{amount} {unit}".strip()
    if dtype == "time" and isinstance(val, dict):
        return str(val.get("time") or "")
    if isinstance(val, (str, int, float)):
        return str(val)
    return str(val)


def _parse_live_statements(entity: dict[str, Any]) -> list[dict[str, str]]:
    claims = entity.get("claims") or {}
    out: list[dict[str, str]] = []
    for pid, claim_list in claims.items():
        if not isinstance(claim_list, list):
            continue
        for claim in claim_list:
            mainsnak = (claim or {}).get("mainsnak") or {}
            if mainsnak.get("snaktype") != "value":
                continue
            dv = mainsnak.get("datavalue") or {}
            out.append({
                "property_id": str(pid),
                "value": _format_datavalue(dv),
                "value_type": str(dv.get("type") or ""),
            })
    return out


def _studio_stmt_value(stmt: dict[str, Any]) -> str:
    vid = stmt.get("value_id")
    if vid:
        return str(vid)
    val = stmt.get("value")
    if val is None:
        return ""
    if isinstance(val, dict):
        return str(val.get("text") or val.get("id") or val)
    return str(val).strip('"')


def _stmt_key(stmt: dict[str, Any]) -> str:
    pid = str(stmt.get("property_id") or stmt.get("property") or "")
    return f"{pid}|{_studio_stmt_value(stmt)}"


def _studio_label(item: dict[str, Any]) -> str:
    labels = item.get("labels") or {}
    return str(labels.get("en") or labels.get("he") or item.get("local_id") or "")


def build_compare(
    studio_item: dict[str, Any],
    live_entity: dict[str, Any],
    qid: str,
) -> WikidataCompareResponse:
    wd_labels = _lang_map(live_entity.get("labels"))
    wd_descs = _lang_map(live_entity.get("descriptions"))
    st_labels = {k: str(v) for k, v in (studio_item.get("labels") or {}).items() if v}
    st_descs = {k: str(v) for k, v in (studio_item.get("descriptions") or {}).items() if v}

    rows: list[CompareFieldRow] = []

    for lang in sorted(set(wd_labels) | set(st_labels)):
        wv, sv = wd_labels.get(lang), st_labels.get(lang)
        if wv and sv and wv == sv:
            status: CompareStatus = "same"
        elif wv and sv:
            status = "conflict"
        elif wv:
            status = "wikidata_only"
        else:
            status = "studio_only"
        rows.append(CompareFieldRow(
            kind="label",
            key=lang,
            label=f"Label ({lang})",
            wikidata_value=wv,
            studio_value=sv,
            status=status,
        ))

    for lang in sorted(set(wd_descs) | set(st_descs)):
        wv, sv = wd_descs.get(lang), st_descs.get(lang)
        if wv and sv and wv == sv:
            status = "same"
        elif wv and sv:
            status = "conflict"
        elif wv:
            status = "wikidata_only"
        else:
            status = "studio_only"
        rows.append(CompareFieldRow(
            kind="description",
            key=lang,
            label=f"Description ({lang})",
            wikidata_value=wv,
            studio_value=sv,
            status=status,
        ))

    live_stmts = _parse_live_statements(live_entity)
    studio_stmts = list(studio_item.get("statements") or [])
    live_by_key: dict[str, dict[str, str]] = {}
    for s in live_stmts:
        live_by_key[_stmt_key(s)] = s

    studio_by_key: dict[str, tuple[int, dict[str, Any]]] = {}
    for i, s in enumerate(studio_stmts):
        if isinstance(s, dict):
            studio_by_key[_stmt_key(s)] = (i, s)

    all_keys = sorted(set(live_by_key) | set(studio_by_key))
    for key in all_keys:
        pid = key.split("|", 1)[0]
        live_s = live_by_key.get(key)
        studio_pair = studio_by_key.get(key)
        studio_idx = studio_pair[0] if studio_pair else None
        studio_s = studio_pair[1] if studio_pair else None
        wv = _studio_stmt_value(live_s) if live_s else None
        sv = _studio_stmt_value(studio_s) if studio_s else None
        if live_s and studio_s:
            status = "same" if wv == sv else "conflict"
        elif live_s:
            status = "wikidata_only"
        else:
            status = "studio_only"
        rows.append(CompareFieldRow(
            kind="statement",
            key=key,
            label=pid,
            wikidata_value=wv,
            studio_value=sv,
            status=status,
            studio_statement_index=studio_idx,
        ))

    conflicts = [r for r in rows if r.status == "conflict"]
    return WikidataCompareResponse(
        qid=qid.strip().upper(),
        studio_local_id=str(studio_item.get("local_id") or ""),
        wikidata=WikidataEntitySummary(
            qid=qid.strip().upper(),
            labels=wd_labels,
            descriptions=wd_descs,
            statement_count=len(live_stmts),
        ),
        studio_label=_studio_label(studio_item),
        rows=rows,
        has_conflicts=bool(conflicts),
        conflict_count=len(conflicts),
    )


def apply_compare_choices(
    compare: WikidataCompareResponse,
    *,
    policy: str,
    choices: list[dict[str, Any]] | None = None,
    studio_statements: list[dict[str, Any]],
) -> ApplyCompareChoicesResponse:
    """Turn compare resolutions into override payload fragments."""
    choice_map: dict[tuple[str, str], str] = {}
    for ch in choices or []:
        kind = str(ch.get("kind") or "")
        key = str(ch.get("key") or "")
        src = str(ch.get("source") or "")
        if kind and key and src in ("wikidata", "studio"):
            choice_map[(kind, key)] = src

    def pick(kind: str, key: str, wd: str | None, st: str | None) -> str | None:
        if policy == "wikidata":
            return wd if wd is not None else st
        if policy == "studio":
            return st if st is not None else wd
        src = choice_map.get((kind, key))
        if src == "wikidata":
            return wd
        if src == "studio":
            return st
        return st if st is not None else wd

    labels: dict[str, str | None] = {}
    descriptions: dict[str, str | None] = {}
    remove_statements: list[int] = []
    add_statements: list[dict[str, Any]] = []
    statement_edits: dict[str, dict[str, Any]] = {}

    for row in compare.rows:
        if row.kind == "label":
            chosen = pick("label", row.key, row.wikidata_value, row.studio_value)
            if chosen is not None:
                labels[row.key] = chosen
            elif row.studio_value is not None:
                labels[row.key] = None
        elif row.kind == "description":
            chosen = pick("description", row.key, row.wikidata_value, row.studio_value)
            if chosen is not None:
                descriptions[row.key] = chosen
            elif row.studio_value is not None:
                descriptions[row.key] = None
        elif row.kind == "statement":
            src = "wikidata" if policy == "wikidata" else "studio" if policy == "studio" else choice_map.get(("statement", row.key), "studio")
            if row.status == "same":
                continue
            if src == "wikidata":
                if row.studio_statement_index is not None and row.status in ("conflict", "studio_only"):
                    remove_statements.append(row.studio_statement_index)
                if row.status in ("wikidata_only", "conflict") and row.wikidata_value:
                    pid = row.key.split("|", 1)[0]
                    add_statements.append({
                        "property_id": pid,
                        "property": pid,
                        "value": row.wikidata_value,
                        "value_type": "item" if row.wikidata_value.startswith("Q") else "string",
                        "value_id": row.wikidata_value if row.wikidata_value.startswith("Q") else None,
                    })
            else:
                if row.status == "wikidata_only":
                    pass
                elif row.studio_statement_index is not None and row.status == "conflict":
                    pass

    return ApplyCompareChoicesResponse(
        labels=labels,
        descriptions=descriptions,
        remove_statements=sorted(set(remove_statements)),
        add_statements=add_statements,
        statement_edits=statement_edits,
    )
