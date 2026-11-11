"""Exact historical repairs with ownership, source, revision, and receipt gates."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from typing import Protocol

import requests

from app.pipeline.marc_ingest import prepare_record_for_pipeline
from app.pipeline.wikidata_gila_repairs import (
    build_gila_repair_proposals,
    entity_digest,
    validate_gila_repair_proposal,
)
from converter.wikidata.catalog_notes import is_catalog_note_placeholder
from converter.wikidata.uploader import WikidataUploader

API_URL = "https://www.wikidata.org/w/api.php"
Journal = Callable[[str, dict[str, object]], Awaitable[None]]
SourceCheck = Callable[[Mapping[str, object], Mapping[str, object]], Awaitable[dict[str, object]]]
PRE_WRITE_REJECTIONS = frozenset(
    {
        "badtoken",
        "notoken",
        "editconflict",
        "maxlag",
        "permissiondenied",
        "readapidenied",
        "writeapidenied",
        "protectedpage",
        "readonly",
        "blocked",
        "assertuserfailed",
        "assertbotfailed",
        "noedit",
        "noedit-anon",
        "ratelimited",
    }
)


def source_colophon(record: Mapping[str, object]) -> str:
    text = str(prepare_record_for_pipeline(deepcopy(dict(record))).get("colophon_text") or "")
    return "" if is_catalog_note_placeholder(text) else text


def validate_source_colophons(
    proposal: Mapping[str, object],
    entity: Mapping[str, object],
    record: Mapping[str, object],
) -> None:
    changes = [
        row
        for row in _objects(proposal.get("changes"))
        if row.get("kind") in {"remove_duplicate_colophon", "preserve_colophon_references"}
    ]
    if not changes:
        return
    qid = str(entity.get("id") or "")
    derived = build_gila_repair_proposals(
        {qid: entity}, colophons_by_qid={qid: source_colophon(record)}
    )
    allowed = [row for candidate in derived for row in _objects(candidate.get("changes"))]
    if any(change not in allowed for change in changes):
        raise ValueError("The colophon repair does not match the current source.")


class WriteRejectedError(RuntimeError):
    """The API confirms rejection without a successful edit."""


class RepairGateway(Protocol):
    async def fetch(self, qid: str) -> dict[str, object]: ...
    async def assert_allowed(self, qid: str) -> None: ...
    async def write(self, qid: str, revision: int, claims: list[dict[str, object]]) -> int: ...


def _objects(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError("Claim data must contain objects.")
    return list(value)


def repair_claims(proposal: Mapping[str, object]) -> list[dict[str, object]]:
    """Send only exact changed claims; never replace the whole entity."""
    payload = []
    for change in _objects(proposal.get("changes")):
        after = change.get("after")
        if after is None:
            payload.append({"id": str(change["claim_id"]), "remove": ""})
        elif isinstance(after, dict):
            payload.append(deepcopy(after))
        else:
            raise ValueError("A repair needs an exact after claim.")
    if not payload:
        raise ValueError("An empty repair cannot write.")
    return payload


def expected_entity(
    proposal: Mapping[str, object],
    entity: Mapping[str, object],
) -> dict[str, object]:
    expected = deepcopy(dict(entity))
    claims = expected.get("claims")
    if not isinstance(claims, dict):
        raise ValueError("The entity needs claims.")
    changes = {str(row["claim_id"]): row.get("after") for row in _objects(proposal.get("changes"))}
    for pid, rows in list(claims.items()):
        claims[pid] = [
            deepcopy(changes.get(str(row.get("id")), row))
            for row in _objects(rows)
            if str(row.get("id")) not in changes or changes[str(row["id"])] is not None
        ]
        if not claims[pid]:
            del claims[pid]
    return expected


def semantic_entity(value: object) -> object:
    """Ignore transport hashes and ordering, while retaining duplicate counts."""
    if isinstance(value, Mapping):
        return {
            key: semantic_entity(member)
            for key, member in value.items()
            if key not in {"hash", "snaks-order", "qualifiers-order", "lastrevid", "modified"}
        }
    if isinstance(value, list):
        return sorted(
            (semantic_entity(member) for member in value),
            key=lambda member: json.dumps(member, sort_keys=True),
        )
    return value


async def execute_repair(
    proposal: Mapping[str, object],
    gateway: RepairGateway,
    *,
    authorized_qids: frozenset[str],
    source_check: SourceCheck,
    journal: Journal,
    apply: bool = False,
    prior_outcome: str = "not_sent",
) -> dict[str, object]:
    qid = str(proposal.get("qid") or "")
    if qid not in authorized_qids:
        raise ValueError("The QID is outside the authorized batch.")
    before = await gateway.fetch(qid)
    validate_gila_repair_proposal(proposal, before, prior_outcome=prior_outcome)
    source_evidence = await source_check(before, proposal)
    await gateway.assert_allowed(qid)
    receipt: dict[str, object] = {
        "qid": qid,
        "before_revision": before["lastrevid"],
        "entity_sha256": proposal["entity_sha256"],
        "status": "ready",
        "corrected": False,
        "after_revision": None,
        "proposal": deepcopy(dict(proposal)),
        "proposal_sha256": entity_digest(proposal),
        "source_evidence": source_evidence,
    }
    if not apply:
        return receipt
    await journal("intent", {**receipt, "status": "intent"})
    write_started = False
    try:
        fresh = await gateway.fetch(qid)
        validate_gila_repair_proposal(proposal, fresh)
        if await source_check(fresh, proposal) != source_evidence:
            raise ValueError("The source evidence changed after the intent.")
        await gateway.assert_allowed(qid)
        revision = fresh.get("lastrevid")
        if type(revision) is not int or revision <= 0:
            raise ValueError("The repair needs a positive revision.")
        expected = expected_entity(proposal, fresh)
        write_started = True
        after_revision = await gateway.write(qid, revision, repair_claims(proposal))
        after = await gateway.fetch(qid)
        if (
            after_revision <= revision
            or after.get("lastrevid") != after_revision
            or semantic_entity(after) != semantic_entity(expected)
        ):
            raise RuntimeError("The live state does not match the complete intended result.")
        receipt.update(status="verified", corrected=True, after_revision=after_revision)
        await journal("result", receipt)
        return receipt
    except Exception as error:
        receipt.update(
            status="uncertain"
            if write_started and not isinstance(error, WriteRejectedError)
            else "blocked",
            corrected=False,
            error_type=type(error).__name__,
        )
        await journal("result", receipt)
        return receipt


class LiveRepairGateway:
    """Reuse the normal uploader authentication and ownership guards."""

    def __init__(self, uploader: WikidataUploader) -> None:
        self.uploader = uploader

    async def fetch(self, qid: str) -> dict[str, object]:
        def read() -> dict[str, object]:
            self.uploader._prefetched_entities.pop(qid, None)
            entities = self.uploader._wbgetentities(
                [qid], props="info|labels|descriptions|aliases|claims|sitelinks"
            )
            entity = entities.get(qid)
            if not isinstance(entity, dict) or "missing" in entity or entity.get("id") != qid:
                raise ValueError("The exact live entity is unavailable.")
            return entity

        return await asyncio.to_thread(read)

    async def assert_allowed(self, qid: str) -> None:
        def check() -> None:
            self.uploader._check_moratorium_for_live()
            self.uploader.ensure_authenticated()
            self.uploader._is_our_item_cache.pop(qid, None)
            self.uploader._creator_cache.pop(qid, None)
            self.uploader._foreign_accept_qids.clear()
            self.uploader._assert_modifiable(qid, stage="exact_repair")
            self.uploader._bot_exclusion_cache.pop(qid, None)
            if self.uploader._bot_excluded(qid):
                raise ValueError("The target excludes bot edits.")

        await asyncio.to_thread(check)

    async def write(self, qid: str, revision: int, claims: list[dict[str, object]]) -> int:
        def send() -> int:
            login = self.uploader._login
            if login is None or login.mediawiki_api_url != API_URL:
                raise ValueError("The repair requires the normal live Wikidata session.")
            session = login.get_session()
            if not isinstance(session, requests.Session):
                raise ValueError("The authenticated session is unavailable.")
            if session.get_adapter(API_URL).max_retries.total != 0:
                raise ValueError("The repair transport must not retry writes.")
            token = login.get_edit_token()
            if not token:
                raise ValueError("The edit token is unavailable.")
            self.uploader._rate_limit()
            self.uploader._assert_modifiable(qid, stage="exact_repair_pre_write")
            response = session.post(
                API_URL,
                data={
                    "action": "wbeditentity",
                    "id": qid,
                    "baserevid": revision,
                    "data": json.dumps({"claims": claims}, ensure_ascii=False),
                    "summary": (
                        "Repair exact duplicate source references or "
                        "a source-verified duplicate colophon"
                    ),
                    "token": token,
                    "format": "json",
                    "maxlag": 20,
                },
                allow_redirects=False,
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise RuntimeError("The write response is unavailable.")
            if result.get("error"):
                error = result["error"]
                if isinstance(error, dict) and error.get("code") in PRE_WRITE_REJECTIONS:
                    raise WriteRejectedError("The API rejected the exact repair before the edit.")
                raise RuntimeError("The API returned an uncertain write outcome.")
            entity = result.get("entity")
            if (
                result.get("success") != 1
                or not isinstance(entity, dict)
                or entity.get("id") != qid
                or type(entity.get("lastrevid")) is not int
            ):
                raise RuntimeError("The write did not return a confirmed entity revision.")
            return entity["lastrevid"]

        return await asyncio.to_thread(send)
