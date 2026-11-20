"""A dated review of equivalent citations on the remaining project items."""

import json
from pathlib import Path

from app.pipeline.wikidata_gila_repairs import entity_digest
from converter.wikidata.reference_retrieval import current_reference_indices

_INVENTORY = Path(__file__).resolve().parents[1] / "data/gila_remaining_reference_scope.json"
GILA_CREATOR_UID = 6935067
GILA_CREATOR_NAME = "Alexander Goldberg IL"


def remaining_reference_inventory() -> dict:
    return json.loads(_INVENTORY.read_text(encoding="utf-8"))


def reviewed_reference_evidence(proposal: dict, reviewed: dict) -> dict:
    """Require the audited claim, then prove that every citation group survives."""
    changes = proposal.get("changes")
    if proposal.get("qid") != reviewed["qid"] or not reviewed["creator_matches"] or not changes:
        raise ValueError("The proposal is outside the reviewed reference scope.")
    claim_digests = {}
    for change in changes:
        before, after = change["before"], change["after"]
        claim_id = before["id"]
        digest = entity_digest(before)
        if reviewed["claims"].get(claim_id) != digest:
            raise ValueError(
                "The reviewed citation changed. Review the current claim before repair."
            )
        references = before["references"]
        kept = [references[index] for index in current_reference_indices(references)]
        if (
            len(kept) >= len(references)
            or change["kind"]
            not in {"deduplicate_exact_references", "coalesce_retrieval_references"}
            or after != {**before, "references": kept}
        ):
            raise ValueError("This repair does not preserve the reviewed citation groups.")
        claim_digests[claim_id] = digest
    return {
        "qid": reviewed["qid"],
        "source_sha256": claim_digests,
        "scope": "reviewed exact citations",
    }
