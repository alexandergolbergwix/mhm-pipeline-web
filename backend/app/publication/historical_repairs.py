"""Exact historical repairs through the normal durable Publication module."""

from collections.abc import AsyncIterator, Mapping
from copy import deepcopy

from app.pipeline.wikidata_gila_execution import (
    RepairGateway,
    SourceCheck,
    WriteRejectedError,
    expected_entity,
    repair_claims,
    semantic_entity,
)
from app.pipeline.wikidata_gila_repairs import entity_digest, validate_gila_repair_proposal
from app.publication.digests import canonical_digest, thaw_json
from app.publication.gateway import GatewayMutation, TargetObservation, WriteOutcome
from app.publication.types import PublicationEntityInput, SourceSnapshotRef, TargetRef

TARGET = TargetRef(site="www.wikidata.org", environment="production")


class RepairProjectionSource:
    def __init__(self, plan: Mapping[str, object], source_check: SourceCheck):
        self.plan = deepcopy(dict(plan))
        self.digest = entity_digest(self.plan)
        self.source_check = source_check
        self.documents = []
        evidence = {row["qid"]: row["source_evidence"] for row in self.plan["preflight"]}
        authorized = frozenset(self.plan["authorized_qids"])
        seen = set()
        for proposal in self.plan["proposals"]:
            qid = str(proposal["qid"])
            if qid not in authorized or qid in seen:
                raise ValueError("The repair target is outside the plan or occurs twice.")
            seen.add(qid)
            before = self.plan["before_entities"][qid]
            validate_gila_repair_proposal(proposal, before)
            self.documents.append(
                {
                    "repair_proposal": proposal,
                    "repair_before": before,
                    "repair_source_evidence": evidence[qid],
                    "statements": [],
                }
            )

    async def project(
        self, source_snapshot, profile
    ) -> AsyncIterator[tuple[PublicationEntityInput, ...]]:
        if (
            source_snapshot.digest != self.digest
            or profile.name != "gila-exact-repair"
            or profile.version != "1"
        ):
            raise ValueError("The repair plan or profile changed.")
        for document in sorted(self.documents, key=lambda row: row["repair_proposal"]["qid"]):
            if (
                await self.source_check(document["repair_before"], document["repair_proposal"])
                != document["repair_source_evidence"]
            ):
                raise ValueError("The repair source changed before preparation.")
            qid = document["repair_proposal"]["qid"]
            yield (
                PublicationEntityInput(
                    entity_key=f"repair:{qid}",
                    entity_type="manuscript",
                    document=document,
                    identity_assertions=(f"qid:{qid}",),
                ),
            )

    async def is_current(self, run_id: str, source_snapshot: SourceSnapshotRef) -> bool:
        if run_id != self.plan["run_id"] or source_snapshot.digest != self.digest:
            return False
        # The immutable plan is the release source. Each actual send separately
        # checks the current MARC evidence for that target, including on resume.
        return True


async def advance_repair_publication(
    plan, gateway, source_check, repository, *, actor_id: str, run_id: str, clock=None
):
    """Resume durable intents. A future retry date leaves the execution paused."""
    import uuid

    from app.publication.core import PublicationModule
    from app.publication.types import (
        DryRunCommand,
        EntitySelection,
        PrepareRequest,
        ProfileRef,
        PublishCommand,
        ResumeCommand,
        ReviewCommand,
        SummaryQuery,
    )

    if plan.get("actor_user_id") != actor_id or plan.get("run_id") != run_id:
        raise ValueError("The repair plan belongs to a different actor or run.")
    source = RepairProjectionSource(plan, source_check)
    credential_ref = f"historical-repair:{actor_id}"
    adapter = RepairPublicationGateway(
        gateway,
        source_check,
        credential_ref=credential_ref,
        authorized_qids=frozenset(plan["authorized_qids"]),
    )
    module = PublicationModule(
        projection_source=source, repository=repository, gateway=adapter, clock=clock
    )
    operation = await module.prepare(
        PrepareRequest(
            run_id=run_id,
            source_snapshot=SourceSnapshotRef(source.digest, "1", source.digest),
            profile=ProfileRef("gila-exact-repair", "1"),
            target=TARGET,
            actor_id=actor_id,
            idempotency_key=f"repair-prepare:{source.digest}",
        )
    )
    query = SummaryQuery(operation.publication_id)
    summary = await module.read(query)
    if not summary.approval_set_id:
        await module.advance(
            summary.publication_id,
            ReviewCommand(
                release_id=summary.release_id,
                expected_release_digest=summary.release_digest,
                selection=EntitySelection.all(),
                decision="approve",
                actor_id=actor_id,
                reason="Apply the explicitly authorized exact repair plan",
                idempotency_key=f"repair-review:{source.digest}",
            ),
        )
        summary = await module.read(query)
    if not summary.plan_id:
        await module.advance(
            summary.publication_id,
            DryRunCommand(
                approval_set_id=summary.approval_set_id,
                expected_approval_digest=summary.approval_digest,
                credential_ref=credential_ref,
                actor_id=actor_id,
                idempotency_key=f"repair-dry-run:{source.digest}",
            ),
        )
        summary = await module.read(query)
    if summary.plan_blocked_count:
        return summary
    if not summary.execution_id:
        await module.advance(
            summary.publication_id,
            PublishCommand(
                plan_id=summary.plan_id,
                dry_run_receipt_id=summary.dry_run_receipt_id,
                expected_receipt_digest=summary.dry_run_receipt_digest,
                credential_ref=credential_ref,
                actor_id=actor_id,
                idempotency_key=f"repair-publish:{source.digest}",
            ),
        )
    summary = await module.read(query)
    if summary.execution_status in {"paused", "running", "queued"}:
        await module.advance(
            summary.publication_id,
            ResumeCommand(
                execution_id=summary.execution_id,
                credential_ref=credential_ref,
                actor_id=actor_id,
                idempotency_key=f"repair-resume:{uuid.uuid4()}",
            ),
        )
    return await module.read(query)


class RepairPublicationGateway:
    """Bind one authenticated repair gateway to its credential and exact plan."""

    def __init__(
        self,
        gateway: RepairGateway,
        source_check: SourceCheck,
        *,
        credential_ref: str,
        authorized_qids: frozenset[str],
    ):
        self.gateway = gateway
        self.source_check = source_check
        self.credential_ref = credential_ref
        self.authorized_qids = authorized_qids

    async def open(self, credential_ref: str, target: TargetRef):
        if credential_ref != self.credential_ref or target != TARGET:
            raise ValueError("The repair credential or target changed.")
        return self

    def _document(self, mutation):
        document = thaw_json(mutation.document)
        qid = document["repair_proposal"]["qid"]
        if (
            mutation.action != "update"
            or mutation.allow_foreign_update
            or mutation.target_qid != qid
            or qid not in self.authorized_qids
            or mutation.entity_key != f"repair:{qid}"
        ):
            raise ValueError("The exact repair target changed or is unauthorized.")
        validate_gila_repair_proposal(document["repair_proposal"], document["repair_before"])
        return document

    async def reconcile_batch(self, entities):
        rows = []
        for entity in entities:
            document = thaw_json(entity.document)
            qid = document["repair_proposal"]["qid"]
            try:
                if qid not in self.authorized_qids or entity.entity_key != f"repair:{qid}":
                    raise ValueError("The repair target is outside the authorized plan.")
                await self.gateway.assert_allowed(qid)
                before = await self.gateway.fetch(qid)
                validate_gila_repair_proposal(document["repair_proposal"], before)
                if (
                    await self.source_check(before, document["repair_proposal"])
                    != document["repair_source_evidence"]
                ):
                    raise ValueError("The source evidence changed.")
                rows.append(
                    TargetObservation.present_owned(
                        entity.entity_key,
                        qid=qid,
                        fingerprint=entity_digest(before),
                        remote_revision=before["lastrevid"],
                    )
                )
            except Exception as exc:  # Read or proof failures never authorize a write.
                rows.append(TargetObservation.unknown(entity.entity_key, str(exc)))
        return tuple(rows)

    async def compile_mutation(
        self, entity, *, action, target_qid, expected_revision=None, allow_foreign_update=False
    ):
        mutation = GatewayMutation(
            entity_key=entity.entity_key,
            entity_type=entity.entity_type,
            action=action,
            target_qid=target_qid,
            expected_revision=expected_revision,
            allow_foreign_update=allow_foreign_update,
            entity_digest=entity.entity_digest,
            document=entity.document,
            identity_assertions=entity.identity_assertions,
            payload_digest=canonical_digest(
                {
                    "entity": entity.entity_digest,
                    "qid": target_qid,
                    "revision": expected_revision,
                    "action": action,
                }
            ),
        )
        self._document(mutation)
        return mutation

    async def write(self, request):
        sent = False
        try:
            document = self._document(request.mutation)
            proposal = document["repair_proposal"]
            qid = proposal["qid"]
            before = await self.gateway.fetch(qid)
            validate_gila_repair_proposal(proposal, before)
            if before["lastrevid"] != request.mutation.expected_revision:
                raise ValueError("The public revision changed.")
            if await self.source_check(before, proposal) != document["repair_source_evidence"]:
                raise ValueError("The source evidence changed.")
            await self.gateway.assert_allowed(qid)
            if await self.source_check(before, proposal) != document["repair_source_evidence"]:
                raise ValueError("The source changed during the creator proof.")
            if request.authorize_send is None or not await request.authorize_send():
                return WriteOutcome.blocked("The Publication approval or source changed.")
            sent = True
            revision = await self.gateway.write(qid, before["lastrevid"], repair_claims(proposal))
            after = await self.gateway.fetch(qid)
            if await self.source_check(before, proposal) != document["repair_source_evidence"]:
                return WriteOutcome.outcome_unknown("The source changed before the repair receipt.")
            if revision <= before["lastrevid"] or after.get("lastrevid") != revision:
                return WriteOutcome.outcome_unknown(
                    "The current public revision does not confirm the response."
                )
            if semantic_entity(after) != semantic_entity(expected_entity(proposal, before)):
                return WriteOutcome.outcome_unknown(
                    "The full public entity does not match the exact repair."
                )
            return WriteOutcome.succeeded(qid=qid, fingerprint=entity_digest(after))
        except WriteRejectedError as exc:
            if exc.retry_after_seconds is not None:
                return WriteOutcome.pre_send_retryable(
                    str(exc), retry_after_seconds=exc.retry_after_seconds
                )
            return WriteOutcome.blocked(str(exc))
        except Exception as exc:
            return (
                WriteOutcome.outcome_unknown(str(exc)) if sent else WriteOutcome.blocked(str(exc))
            )

    async def recover_ambiguous(self, request):
        try:
            document = self._document(request.mutation)
            proposal = document["repair_proposal"]
            qid = proposal["qid"]
            await self.gateway.assert_allowed(qid)
            if (
                await self.source_check(document["repair_before"], proposal)
                != document["repair_source_evidence"]
            ):
                return WriteOutcome.outcome_unknown("The source changed before recovery.")
            after = await self.gateway.fetch(qid)
            before = document["repair_before"]
            if after.get("lastrevid", 0) > before["lastrevid"] and semantic_entity(
                after
            ) == semantic_entity(expected_entity(proposal, before)):
                return WriteOutcome.succeeded(qid=qid, fingerprint=entity_digest(after))
            return WriteOutcome.outcome_unknown(
                "The public entity does not prove the uncertain repair. Do not resend."
            )
        except Exception as exc:
            return WriteOutcome.outcome_unknown(str(exc))
