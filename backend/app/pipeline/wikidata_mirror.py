"""Store complete public candidates and filter their active facts locally."""

import hashlib
import json
import logging
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models.wikidata_mirror import WikidataMirrorEntity, WikidataMirrorFact

logger = logging.getLogger(__name__)
QID = re.compile(r"Q[1-9][0-9]*\Z")
PID = re.compile(r"P[1-9][0-9]*\Z")
MAX_AGE = timedelta(days=7)


def value_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def name_key(value: str) -> str:
    return " ".join(value.split()).casefold()


def indexed_facts(entity: dict) -> set[tuple[str, str]]:
    """Retain every source field in JSON; index only active value snaks."""
    facts = set()
    for pid, claims in entity["claims"].items():
        if not PID.fullmatch(pid) or not isinstance(claims, list):
            raise ValueError("The entity has invalid claims.")
        for claim in claims:
            if not isinstance(claim, dict):
                raise ValueError("The entity has an invalid statement.")
            if claim.get("rank", "normal") not in {"normal", "preferred"}:
                continue
            snak = claim.get("mainsnak") or {}
            if snak.get("snaktype") != "value":
                continue
            value = (snak.get("datavalue") or {}).get("value")
            if isinstance(value, dict):
                value = (
                    value.get("id") or value.get("text") or value.get("time") or value.get("amount")
                )
            if isinstance(value, str) and value.strip():
                facts.add((pid, value.strip()))
                if pid == "P1476":
                    facts.add(("@name", name_key(value)))
    for row in entity["labels"].values():
        if isinstance(row, dict) and isinstance(row.get("value"), str):
            facts.add(("@name", name_key(row["value"])))
    for rows in entity["aliases"].values():
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and isinstance(row.get("value"), str):
                facts.add(("@name", name_key(row["value"])))
    return {(pid, value) for pid, value in facts if value}


def validate_entity(entity: dict) -> None:
    if not QID.fullmatch(str(entity.get("id", ""))):
        raise ValueError("The snapshot needs an exact public QID.")
    if entity.get("type") != "item" or "missing" in entity or "redirect" in entity:
        raise ValueError("The snapshot is not a live item.")
    if type(entity.get("lastrevid")) is not int or entity["lastrevid"] <= 0:
        raise ValueError("The snapshot needs a positive public revision.")
    if any(
        not isinstance(entity.get(key), dict)
        for key in ("labels", "descriptions", "aliases", "claims", "sitelinks")
    ):
        raise ValueError("The snapshot lacks complete entity fields.")


async def store_entities(db, entities: list[dict], *, observed_at: datetime, source: str) -> int:
    """Atomically replace indexes only for accepted, non-regressive snapshots."""
    if observed_at.tzinfo is None or observed_at > datetime.now(UTC) + timedelta(minutes=5):
        raise ValueError("The snapshot observation time is invalid.")
    prepared = {}
    facts = {}
    for entity in entities:
        validate_entity(entity)
        qid = entity["id"]
        encoded = json.dumps(entity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        row = {
            "qid": qid,
            "revision": entity["lastrevid"],
            "document": entity,
            "content_hash": value_hash(encoded),
            "observed_at": observed_at,
            "source": source,
        }
        previous = prepared.get(qid)
        if previous and previous["revision"] > row["revision"]:
            continue
        if (
            previous
            and previous["revision"] == row["revision"]
            and previous["content_hash"] != row["content_hash"]
        ):
            raise ValueError("One revision has conflicting snapshot content.")
        prepared[qid] = row
        facts[qid] = indexed_facts(entity)
    if not prepared:
        return 0
    dialect = db.get_bind().dialect.name
    insert = sqlite_insert if dialect == "sqlite" else pg_insert
    table = WikidataMirrorEntity
    statement = insert(table).values(list(prepared.values()))
    incoming = statement.excluded
    statement = statement.on_conflict_do_update(
        index_elements=[table.qid],
        set_={
            key: getattr(incoming, key)
            for key in ("revision", "document", "content_hash", "observed_at", "source")
        },
        where=and_(
            incoming.observed_at >= table.observed_at,
            or_(
                incoming.revision > table.revision,
                and_(
                    incoming.revision == table.revision, incoming.content_hash == table.content_hash
                ),
            ),
        ),
    ).returning(table.qid)
    accepted = list((await db.execute(statement)).scalars())
    if not accepted:
        return 0
    await db.execute(delete(WikidataMirrorFact).where(WikidataMirrorFact.qid.in_(accepted)))
    rows = [
        {"qid": qid, "property_id": pid, "value_hash": value_hash(value), "value": value}
        for qid in accepted
        for pid, value in sorted(facts[qid])
    ]
    for start in range(0, len(rows), 500):
        await db.execute(insert(WikidataMirrorFact).values(rows[start : start + 500]))
    await db.flush()
    return len(accepted)


async def filter_entities(
    db,
    filters: list[tuple[str, str]],
    *,
    limit: int = 100,
    include_stale: bool = False,
    now: datetime | None = None,
) -> list:
    """AND all filters in local SQL. An empty result proves no global absence."""
    if not filters or not 1 <= limit <= 500:
        raise ValueError("Use at least one filter and a limit from 1 to 500.")
    statement = select(WikidataMirrorEntity)
    if not include_stale:
        statement = statement.where(
            WikidataMirrorEntity.observed_at >= (now or datetime.now(UTC)) - MAX_AGE
        )
    for pid, value in filters:
        if (pid != "@name" and not PID.fullmatch(pid)) or not value.strip():
            raise ValueError("Each filter needs an exact property and a non-empty value.")
        normalized = name_key(value) if pid == "@name" else value.strip()
        statement = statement.where(
            select(WikidataMirrorFact.qid)
            .where(
                WikidataMirrorFact.qid == WikidataMirrorEntity.qid,
                WikidataMirrorFact.property_id == pid,
                WikidataMirrorFact.value_hash == value_hash(normalized),
                WikidataMirrorFact.value == normalized,
            )
            .exists()
        )
    return list(
        (await db.execute(statement.order_by(WikidataMirrorEntity.qid).limit(limit))).scalars()
    )


async def local_probe_candidates(db_factory, pairs: list[tuple[str, str]]) -> dict:
    """Positive evidence only. Require current identity proof before a link choice."""
    if db_factory is None or not pairs:
        return {}
    requirements = {}
    anchors = {}
    for pid, value in pairs:
        if pid.startswith("label:"):
            filters = [("@name", value), ("P31", pid.removeprefix("label:"))]
        elif pid == "title+P31":
            title, separator, qid = value.partition("␟")
            if not separator:
                continue
            filters = [("@name", title), ("P31", qid)]
        elif "+" in pid:
            properties, values = pid.split("+"), value.split("␟")
            if len(properties) != len(values):
                continue
            filters = list(zip(properties, values, strict=True))
        elif PID.fullmatch(pid):
            filters = [(pid, value)]
        else:
            continue
        normalized = [
            (prop, name_key(text) if prop == "@name" else text.strip()) for prop, text in filters
        ]
        requirements[(pid, value)] = set(normalized)
        anchor = normalized[0] if normalized[0][0] == "@name" else normalized[-1]
        anchors.setdefault(anchor, set()).add((pid, value))
    found = {}
    cutoff = datetime.now(UTC) - MAX_AGE
    try:
        async with db_factory() as db:
            candidates = {}
            anchor_list = list(anchors)
            for start in range(0, len(anchor_list), 200):
                selected = anchor_list[start : start + 200]
                conditions = [
                    and_(
                        WikidataMirrorFact.property_id == pid,
                        WikidataMirrorFact.value_hash == value_hash(value),
                        WikidataMirrorFact.value == value,
                    )
                    for pid, value in selected
                ]
                rows = (
                    await db.execute(
                        select(
                            WikidataMirrorFact.qid,
                            WikidataMirrorFact.property_id,
                            WikidataMirrorFact.value,
                        )
                        .join(WikidataMirrorEntity)
                        .where(WikidataMirrorEntity.observed_at >= cutoff, or_(*conditions))
                        .order_by(WikidataMirrorFact.qid)
                        .limit(20000)
                    )
                ).all()
                counts = {}
                for qid, pid, value in rows:
                    anchor = (pid, value)
                    if counts.get(anchor, 0) >= 100:
                        continue
                    counts[anchor] = counts.get(anchor, 0) + 1
                    candidates.setdefault(qid, set()).update(anchors[anchor])
            identifiers = sorted(candidates)
            for start in range(0, len(identifiers), 100):
                rows = (
                    await db.execute(
                        select(WikidataMirrorEntity).where(
                            WikidataMirrorEntity.qid.in_(identifiers[start : start + 100]),
                            WikidataMirrorEntity.observed_at >= cutoff,
                        )
                    )
                ).scalars()
                for row in rows:
                    facts = indexed_facts(row.document)
                    for key in candidates[row.qid]:
                        if not requirements[key] <= facts:
                            continue
                        pid, value = key
                        found.setdefault(key, []).append(
                            {
                                "qid": row.qid,
                                "label": next(
                                    (
                                        row.document["labels"][lang]["value"]
                                        for lang in ("he", "en")
                                        if lang in row.document["labels"]
                                    ),
                                    "",
                                ),
                                "matched_on": f"local:{pid}={value}",
                                "requires_curator_confirmation": "true",
                                "evidence_source": "local_mirror",
                                "revision": str(row.revision),
                                "coverage": "partial",
                                "candidate_limit": "100",
                            }
                        )
            await db.rollback()
    except Exception as error:
        logger.warning("Local Wikidata candidate read failed: %s", type(error).__name__)
        return {}
    return found
