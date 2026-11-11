"""Historical repair execution must fail closed before and after its one write."""

from copy import deepcopy

import pytest

from app.pipeline.wikidata_gila_execution import WriteRejectedError, execute_repair, expected_entity
from app.pipeline.wikidata_gila_repairs import build_gila_repair_proposals


def entity_fixture() -> dict[str, object]:
    reference = {"snaks": {"P3959": [{"snaktype": "value", "datavalue": {"value": "990001"}}]}}
    return {
        "id": "Q123",
        "type": "item",
        "lastrevid": 10,
        "labels": {"en": {"language": "en", "value": "A manuscript"}},
        "claims": {
            "P217": [
                {
                    "id": "Q123$shelfmark",
                    "type": "statement",
                    "rank": "normal",
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P217",
                        "datavalue": {"value": "Ms. 1"},
                    },
                    "references": [reference, deepcopy(reference)],
                }
            ]
        },
    }


class FakeGateway:
    def __init__(self, proposal: dict[str, object]) -> None:
        self.entity = entity_fixture()
        self.proposal = proposal
        self.writes = 0
        self.allowed = True
        self.error: Exception | None = None
        self.extra_change = False
        self.reads = 0
        self.stale_second_read = False

    async def fetch(self, qid: str) -> dict[str, object]:
        self.reads += 1
        result = deepcopy(self.entity)
        if self.stale_second_read and self.reads == 2:
            result["lastrevid"] = 11
        return result

    async def assert_allowed(self, qid: str) -> None:
        if not self.allowed:
            raise ValueError("Not owned")

    async def write(self, qid: str, revision: int, claims: list[dict[str, object]]) -> int:
        self.writes += 1
        if self.error:
            raise self.error
        self.entity = expected_entity(self.proposal, self.entity)
        self.entity["lastrevid"] = 11
        if self.extra_change:
            self.entity["labels"] = {"en": {"language": "en", "value": "Unexpected label"}}
        return 11


async def source_check(entity: object, proposal: object) -> dict[str, object]:
    return {"control_number": "990001"}


@pytest.mark.asyncio
async def test_verified_repair_has_durable_intent_and_complete_readback() -> None:
    proposal = build_gila_repair_proposals({"Q123": entity_fixture()})[0]
    gateway = FakeGateway(proposal)
    events = []

    async def journal(phase: str, receipt: dict[str, object]) -> None:
        events.append((phase, deepcopy(receipt)))

    receipt = await execute_repair(
        proposal,
        gateway,
        authorized_qids=frozenset({"Q123"}),
        source_check=source_check,
        journal=journal,
        apply=True,
    )
    assert receipt["corrected"] is True
    assert receipt["status"] == "verified"
    assert gateway.writes == 1
    assert events[0][0] == "intent" and events[0][1]["corrected"] is False
    assert events[0][1]["proposal"] == proposal
    assert len(events[0][1]["proposal_sha256"]) == 64
    assert events[0][1]["source_evidence"] == {"control_number": "990001"}
    assert events[1][0] == "result"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["ownership", "scope", "audit", "stale", "source"])
async def test_failed_pre_write_gate_never_sends(failure: str) -> None:
    proposal = build_gila_repair_proposals({"Q123": entity_fixture()})[0]
    gateway = FakeGateway(proposal)
    gateway.allowed = failure != "ownership"
    gateway.stale_second_read = failure == "stale"

    async def journal(phase: str, receipt: dict[str, object]) -> None:
        if failure == "audit":
            raise RuntimeError("No durable audit")

    async def validate_source(entity: object, proposal: object) -> dict[str, object]:
        if failure == "source":
            raise ValueError("Changed source")
        return {"control_number": "990001"}

    async def attempt() -> dict[str, object]:
        return await execute_repair(
            proposal,
            gateway,
            authorized_qids=frozenset() if failure == "scope" else frozenset({"Q123"}),
            source_check=validate_source,
            journal=journal,
            apply=True,
        )

    if failure == "stale":
        assert (await attempt())["corrected"] is False
    else:
        with pytest.raises((ValueError, RuntimeError)):
            await attempt()
    assert gateway.writes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "changed_readback", "rejected"])
async def test_a_failed_write_has_no_retry_or_false_correction(failure: str) -> None:
    proposal = build_gila_repair_proposals({"Q123": entity_fixture()})[0]
    gateway = FakeGateway(proposal)
    if failure == "timeout":
        gateway.error = TimeoutError("Do not print credential details")
    elif failure == "rejected":
        gateway.error = WriteRejectedError("editconflict")
    else:
        gateway.extra_change = True
    events = []

    async def journal(phase: str, receipt: dict[str, object]) -> None:
        events.append(deepcopy(receipt))

    receipt = await execute_repair(
        proposal,
        gateway,
        authorized_qids=frozenset({"Q123"}),
        source_check=source_check,
        journal=journal,
        apply=True,
    )
    assert gateway.writes == 1
    assert receipt["corrected"] is False
    assert receipt["status"] == ("blocked" if failure == "rejected" else "uncertain")
    assert "Do not print credential details" not in str(events)


@pytest.mark.asyncio
async def test_preflight_and_prior_uncertainty_cannot_write() -> None:
    proposal = build_gila_repair_proposals({"Q123": entity_fixture()})[0]
    gateway = FakeGateway(proposal)

    async def journal(phase: str, receipt: dict[str, object]) -> None:
        raise AssertionError("Preflight must not persist a write intent")

    receipt = await execute_repair(
        proposal,
        gateway,
        authorized_qids=frozenset({"Q123"}),
        source_check=source_check,
        journal=journal,
    )
    assert receipt["status"] == "ready" and gateway.writes == 0
    with pytest.raises(ValueError, match="uncertain"):
        await execute_repair(
            proposal,
            gateway,
            authorized_qids=frozenset({"Q123"}),
            source_check=source_check,
            journal=journal,
            apply=True,
            prior_outcome="uncertain",
        )
    assert gateway.writes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,rejected",
    [
        ("editconflict", True),
        ("maxlag", True),
        ("internal_api_error_RuntimeException", False),
        ("unknown-error", False),
    ],
)
async def test_live_transport_classifies_errors_without_a_second_post(
    code: str, rejected: bool
) -> None:
    import json
    from types import SimpleNamespace
    from unittest.mock import Mock

    import requests

    from app.pipeline.wikidata_gila_execution import API_URL, LiveRepairGateway

    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps({"error": {"code": code}}).encode()
    session = requests.Session()
    session.post = Mock(return_value=response)
    login = SimpleNamespace(
        mediawiki_api_url=API_URL,
        get_session=lambda: session,
        get_edit_token=lambda: "fixture-edit-token",
    )
    uploader = SimpleNamespace(_login=login, _rate_limit=Mock(), _assert_modifiable=Mock())
    gateway = LiveRepairGateway(uploader)
    with pytest.raises(WriteRejectedError if rejected else RuntimeError):
        await gateway.write("Q123", 10, [{"id": "Q123$duplicate", "remove": ""}])
    session.post.assert_called_once()
    kwargs = session.post.call_args.kwargs
    assert kwargs["allow_redirects"] is False
    assert kwargs["data"]["baserevid"] == 10
    assert json.loads(kwargs["data"]["data"]) == {
        "claims": [{"id": "Q123$duplicate", "remove": ""}]
    }


def test_apply_requires_source_derived_colophon_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.pipeline.wikidata_gila_execution import validate_source_colophons

    entity = entity_fixture()
    proposal = {
        "changes": [
            {"kind": "remove_duplicate_colophon", "source_text": "forged", "claim_id": "Q123$bad"}
        ]
    }
    monkeypatch.setattr(
        "app.pipeline.wikidata_gila_execution.source_colophon", lambda record: "actual source"
    )
    with pytest.raises(ValueError, match="current source"):
        validate_source_colophons(proposal, entity, {})
