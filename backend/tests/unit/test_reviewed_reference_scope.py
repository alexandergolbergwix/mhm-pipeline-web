from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.pipeline.wikidata_gila_repairs import build_gila_repair_proposals, entity_digest
from app.publication.reviewed_reference_scope import (
    GILA_CREATOR_NAME,
    GILA_CREATOR_UID,
    remaining_reference_inventory,
    reviewed_reference_evidence,
)
from app.publication.wikidata_gateway import CurrentWikidataBoundary, WikidataPermanentError


def test_reviewed_scope_keeps_the_nine_owned_items_separate():
    items = remaining_reference_inventory()["items"]
    assert len(items) == 27
    assert sum(row["baseline_blocks"] for row in items) == 472
    assert sum(row["creator_matches"] for row in items) == 9
    assert sum(row["baseline_blocks"] for row in items if row["creator_matches"]) == 13
    assert all("claims" not in row for row in items if not row["creator_matches"])


def test_reviewed_scope_refuses_changed_claims_and_payloads():
    ref = {
        "snaks": {"P854": [{"property": "P854", "datavalue": {"value": "https://example.org/NLI"}}]}
    }
    claim = {"id": "Q1$A", "references": [ref, deepcopy(ref)]}
    entity = {"id": "Q1", "lastrevid": 1, "claims": {"P31": [claim]}}
    proposal = build_gila_repair_proposals({"Q1": entity})[0]
    reviewed = {"qid": "Q1", "creator_matches": True, "claims": {"Q1$A": entity_digest(claim)}}
    assert reviewed_reference_evidence(proposal, reviewed)["qid"] == "Q1"
    changed = deepcopy(proposal)
    changed["changes"][0]["before"]["rank"] = "preferred"
    with pytest.raises(ValueError, match="reviewed citation changed"):
        reviewed_reference_evidence(changed, reviewed)
    changed = deepcopy(proposal)
    changed["changes"][0]["after"]["rank"] = "preferred"
    with pytest.raises(ValueError, match="preserve the reviewed citation groups"):
        reviewed_reference_evidence(changed, reviewed)
    with pytest.raises(ValueError, match="outside the reviewed"):
        reviewed_reference_evidence(proposal, {**reviewed, "creator_matches": False})


def test_scoped_publication_write_requires_exact_existing_target_and_creator():
    uploader = Mock(_is_test=False)
    uploader._creator_identity_matches.return_value = False
    boundary = CurrentWikidataBoundary(uploader=uploader)
    mutation = SimpleNamespace(
        document={"publication_gila_repair_qid": "Q1"}, target_qid="Q1", action="update"
    )
    with pytest.raises(WikidataPermanentError, match="creator UID"):
        boundary._assert_publication_modifiable(mutation)
    uploader._creator_identity_matches.assert_called_once_with(
        "Q1",
        GILA_CREATOR_NAME,
        required_user_id=GILA_CREATOR_UID,
    )
    uploader._assert_modifiable.assert_not_called()
    mutation.action = "create"
    mutation.target_qid = None
    uploader._creator_identity_matches.return_value = True
    with pytest.raises(WikidataPermanentError, match="exact target"):
        boundary._assert_publication_modifiable(mutation)
