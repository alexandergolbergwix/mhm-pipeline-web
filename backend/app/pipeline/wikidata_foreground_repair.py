"""Reference corrections on one explicitly confirmed item while the user waits."""

import re
import threading
import time

from app.pipeline.wikidata_gila_execution import LiveRepairGateway, repair_claims
from app.pipeline.wikidata_gila_repairs import entity_digest, validate_gila_repair_proposal

MODE = "foreground_reference"
PROFILE = "gila-foreground-reference"


class ForegroundReferenceGateway(LiveRepairGateway):
    def __init__(self, uploader, *, plan, confirmed_qid, confirmed_proposal_sha256,
                 monotonic=time.monotonic):
        proposals = plan.get("proposals", [])
        if (plan.get("execution_mode") != MODE or len(proposals) != 1
                or plan.get("authorized_qids") != [confirmed_qid]
                or not isinstance(confirmed_qid, str)
                or not re.fullmatch(r"Q[1-9][0-9]*", confirmed_qid)):
            raise ValueError("A foreground repair requires one confirmed target.")
        proposal = proposals[0]
        if (proposal.get("qid") != confirmed_qid
                or entity_digest(proposal) != confirmed_proposal_sha256):
            raise ValueError("The foreground proposal differs from the confirmation.")
        validate_gila_repair_proposal(proposal, plan["before_entities"][confirmed_qid])
        changes = proposal.get("changes", [])
        if not changes:
            raise ValueError("A foreground repair needs reference corrections.")
        for change in changes:
            if change.get("kind") not in {
                "deduplicate_exact_references", "coalesce_retrieval_references",
            }:
                raise ValueError("A foreground repair permits only reference corrections.")
            before, after = change["before"], change["after"]
            if {k: v for k, v in before.items() if k != "references"} != {
                k: v for k, v in after.items() if k != "references"
            }:
                raise ValueError("A foreground repair cannot change another claim field.")
        super().__init__(uploader)
        self._plan_digest = entity_digest(plan)
        self._qid = confirmed_qid
        self._revision = proposal["revision"]
        self._claims_digest = entity_digest(repair_claims(proposal))
        self._monotonic = monotonic
        self._deadline = monotonic() + 300
        self._attempted = False
        self._attempt_lock = threading.Lock()

    def assert_plan(self, plan) -> None:
        if entity_digest(plan) != self._plan_digest:
            raise ValueError("The confirmed foreground plan changed.")

    def _write_parameters(self, qid, revision, claims, token) -> dict:
        with self._attempt_lock:
            if self._attempted:
                raise ValueError("The foreground write attempt already occurred.")
            if self._monotonic() >= self._deadline:
                raise ValueError("The foreground confirmation expired.")
            if (qid != self._qid or revision != self._revision
                    or entity_digest(claims) != self._claims_digest):
                raise ValueError("The foreground write differs from its confirmation.")
            parameters = super()._write_parameters(qid, revision, claims, token)
            parameters.pop("maxlag")
            parameters["summary"] = (
                "Consolidate equivalent duplicate source references on one reviewed item"
            )
            self._attempted = True
            return parameters


def profile_for_gateway(plan, gateway) -> str:
    foreground = isinstance(gateway, ForegroundReferenceGateway)
    mode = plan.get("execution_mode")
    if mode == MODE:
        if not foreground:
            raise ValueError("A foreground plan requires its confirmed gateway.")
        gateway.assert_plan(plan)
        return PROFILE
    if foreground or mode is not None:
        raise ValueError("The foreground gateway cannot execute an automatic plan.")
    return "gila-exact-repair"
