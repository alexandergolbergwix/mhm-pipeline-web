"""HTTP contract tests for the run-scoped Wikidata Publication seam."""

from __future__ import annotations

import base64
import uuid

import pytest

from app.models.wikidata_studio_cache import WikidataStudioCache
from app.publication.gateway import FakeWikidataGateway, TargetObservation


def _canonical_item(local_id: str, label: str) -> dict[str, object]:
    return {
        "local_id": local_id,
        "entity_type": "work",
        "labels": {"en": label},
        "descriptions": {"en": "A manuscript work"},
        "aliases": {},
        "statements": [
            {
                "property": "P31",
                "value": "Q47461344",
                "value_type": "item",
            }
        ],
        "existing_qid": None,
        "validation_issues": [],
    }


async def _add_canonical_cache(db_session, run_id, *, items=None) -> None:
    cached_items = items or [
        _canonical_item("work:2", "Work 2"),
        _canonical_item("work:1", "Work 1"),
    ]
    db_session.add(
        WikidataStudioCache(
            run_id=run_id,
            approved_only=True,
            source="canonical",
            input_fingerprint="a" * 64,
            result_items=cached_items,
            quickstatements="",
            summary={
                "total_items": len(cached_items),
                "manuscripts": 0,
                "persons": 0,
                "works": len(cached_items),
                "statements": len(cached_items),
            },
            approved_match_count=0,
            pending_match_count=0,
            used_match_count=0,
            record_count=1,
        )
    )
    await db_session.commit()


async def _prepare_direct(db_session, run_id, actor_id, *, target: str, profile_version="1"):
    from app.publication.credentials import configured_publication_gateway_factory
    from app.publication.runtime import PublicationRuntime
    from app.schemas.publication import PreparePublicationRequest

    response = await PublicationRuntime(
        session=db_session,
        gateway_factory=configured_publication_gateway_factory,
    ).prepare(
        run_id=run_id,
        actor_id=str(actor_id),
        request=PreparePublicationRequest.model_validate(
            {
                "profile_id": "mhm-wikidata",
                "profile_version": profile_version,
                "target": target,
                "source": {
                    "kind": "run",
                    "projection_source": "canonical",
                    "approved_only": True,
                },
            }
        ),
    )
    assert response.publication is not None
    return response.publication


@pytest.mark.asyncio
@pytest.mark.parametrize(("decision", "expected_status"), [("approve", "approved"), ("reject", "rejected")])
async def test_entity_pages_show_review_status_after_review(sample_run, db_session, decision, expected_status):
    run_id = sample_run["run_id"]
    await _add_canonical_cache(db_session, run_id)
    publication = await _prepare_direct(db_session, run_id, sample_run["user_id"], target="test")
    release = publication.current_release
    url = f"/api/runs/{run_id}/wikidata-publications/{publication.publication_id}"
    query = {"type": "entities", "release_id": release.release_id, "limit": 1}
    before = await sample_run["client"].post(f"{url}/read", json={"query": query})
    assert before.status_code == 200, before.text
    assert before.json()["items"][0]["review_status"] == "pending"

    reviewed = await sample_run["client"].post(f"{url}/advance", json={"command": {
        "type": "review", "release_id": release.release_id,
        "expected_release_digest": release.release_digest,
        "selection": {"mode": "eligible_release"}, "decision": decision,
        "reason": "The curator reviewed the Release.",
    }})
    assert reviewed.status_code == 200, reviewed.text

    for expected_id in ("work:1", "work:2"):
        page = await sample_run["client"].post(f"{url}/read", json={"query": query})
        assert page.status_code == 200, page.text
        assert [(row["entity_id"], row["review_status"]) for row in page.json()["items"]] == [
            (expected_id, expected_status)
        ]
        query["cursor"] = page.json()["next_cursor"]
    assert query["cursor"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", [None, "qid", "remote_revision", "entity_digest"])
async def test_foreign_consent_reaches_dry_run_worker(sample_run, db_session, monkeypatch, mismatch):
    from app.pipeline.wikidata_publication_dry_run_job import run_wikidata_publication_dry_run_job

    gateway = FakeWikidataGateway(observations={
        "work:1": TargetObservation.present_foreign("work:1", qid="Q123", remote_revision=7, fingerprint="remote"),
        "work:2": TargetObservation.unknown("work:2", "Lookup timed out"),
    })
    monkeypatch.setattr("app.pipeline.run_job_service.spawn_job", lambda _job_id: None)
    monkeypatch.setattr("app.pipeline.wikidata_publication_dry_run_job.WikidataGatewayAdapter", lambda **kwargs: gateway)
    client = sample_run["client"]
    assert (await client.put("/api/me/api-keys/wikidata_test", json={"value": "Fixture@Test:publication-fixture"})).status_code == 200
    run_id = sample_run["run_id"]
    await _add_canonical_cache(db_session, run_id)
    publication = await _prepare_direct(db_session, run_id, sample_run["user_id"], target="test")
    url = f"/api/runs/{run_id}/wikidata-publications/{publication.publication_id}"
    release = publication.current_release
    reviewed = await client.post(f"{url}/advance", json={"command": {
        "type": "review", "release_id": release.release_id, "expected_release_digest": release.release_digest,
        "selection": {"mode": "eligible_release"}, "decision": "approve", "reason": "Reviewed fixture",
    }})
    assert reviewed.status_code == 200, reviewed.text
    approval = reviewed.json()["publication"]["approval_set"]
    command = {"type": "dry_run", "approval_set_id": approval["approval_set_id"],
        "expected_approval_digest": approval["approval_digest"]}

    async def run_check():
        queued = await client.post(f"{url}/advance", json={"command": command})
        assert queued.status_code == 200, queued.text
        assert queued.json()["operation"] is not None
        await run_wikidata_publication_dry_run_job(uuid.UUID(queued.json()["operation"]["operation_id"]))
        response = await client.post(f"{url}/read", json={"query": {"type": "summary"}})
        assert response.status_code == 200, response.text
        return response.json()["publication"]

    blocked = await run_check()
    actions = blocked["plan"]["blocked_actions"]
    assert actions[1]["consent"] is None
    consent = actions[0]["consent"]
    assert consent["entity_key"] == "work:1"
    assert consent["qid"] == "Q123"
    assert consent["remote_revision"] == 7
    if mismatch:
        consent[mismatch] = {"qid": "Q456", "remote_revision": 8, "entity_digest": "0" * 64}[mismatch]
    command["foreign_qid_consents"] = [consent]
    checked = await run_check()
    assert checked["plan"]["plan_id"] != blocked["plan"]["plan_id"]
    assert checked["plan"]["action_counts"]["update"] == (1 if mismatch is None else 0)
    assert checked["plan"]["action_counts"]["blocked"] == (1 if mismatch is None else 2)
    assert checked["dry_run_receipt"]["status"] == "failed"


@pytest.mark.asyncio
async def test_release_resolves_local_connections_to_existing_targets(sample_run, db_session):
    from app.publication import ProfileRef
    from app.publication.runtime import StudioCacheProjectionSource

    work = _canonical_item("work:1", "Work 1")
    work["statements"].append({
        "property": "P50", "value": "__LOCAL:person:1", "value_type": "item",
    })
    person = _canonical_item("person:1", "Person 1")
    person["entity_type"] = "person"
    person["existing_qid"] = "Q123"
    await _add_canonical_cache(db_session, sample_run["run_id"], items=[work, person])
    source = StudioCacheProjectionSource(db_session)
    snapshot = await source.current_snapshot(
        run_id=sample_run["run_id"], source="canonical", approved_only=True,
    )

    projected = [item async for page in source.project(
        snapshot, ProfileRef(name="mhm-wikidata", version="1"),
    ) for item in page]

    result = next(item for item in projected if item.entity_key == "work:1")
    assert result.document["statements"][-1]["value"] == "Q123"
    assert result.local_references == ()


@pytest.mark.asyncio
async def test_release_refuses_false_identity_from_an_older_studio_cache(sample_run, db_session):
    from app.publication import ProfileRef
    from app.publication.runtime import PublicationSourceError, StudioCacheProjectionSource

    person = _canonical_item("person:1", "A person")
    person.update({
        "entity_type": "person", "labels": {"he": "יוסף בן סעדיה בן יוסף בן דוד אבהר"},
        "ai_verdict": {"overall": "full"},
        "authority_evidence": [{
            "mazal_id": "987007300794605171", "preferred_name_heb": "דנן, סעדיה בן יוסף",
        }],
    })
    person["statements"].append({"property": "P8189", "value": "987007300794605171"})
    await _add_canonical_cache(db_session, sample_run["run_id"], items=[person])
    source = StudioCacheProjectionSource(db_session)
    snapshot = await source.current_snapshot(
        run_id=sample_run["run_id"], source="canonical", approved_only=True,
    )

    with pytest.raises(PublicationSourceError, match="blocking validation"):
        _ = [page async for page in source.project(snapshot, ProfileRef("mhm-wikidata", "1"))]


@pytest.mark.asyncio
@pytest.mark.parametrize("profile_version", ["1", "1-nodes"])
async def test_release_tracks_unresolved_connections_inside_qualifiers(
    sample_run, db_session, profile_version,
):
    from app.publication import ProfileRef
    from app.publication.runtime import StudioCacheProjectionSource

    work = _canonical_item("work:1", "Work 1")
    work["statements"][0]["qualifiers"] = [{
        "property": "P50", "value": "__LOCAL:person:new", "value_type": "item",
    }]
    await _add_canonical_cache(db_session, sample_run["run_id"], items=[work])
    source = StudioCacheProjectionSource(db_session)
    snapshot = await source.current_snapshot(
        run_id=sample_run["run_id"], source="canonical", approved_only=True,
    )

    projected = [item async for page in source.project(
        snapshot, ProfileRef(name="mhm-wikidata", version=profile_version),
    ) for item in page]

    assert projected[0].local_references == ("person:new",)


@pytest.mark.asyncio
async def test_node_release_preserves_deferred_claims_for_a_later_release(sample_run, db_session):
    from app.publication import ProfileRef
    from app.publication.runtime import StudioCacheProjectionSource

    work = _canonical_item("work:1", "Work 1")
    connection = {
        "property": "P50", "value": "__LOCAL:person:new", "value_type": "item",
        "references": [{"property": "P854", "value": "https://example.org/source"}],
    }
    work["statements"].append(connection)
    person = _canonical_item("person:new", "Person 1")
    person["entity_type"] = "person"
    await _add_canonical_cache(db_session, sample_run["run_id"], items=[work, person])
    source = StudioCacheProjectionSource(db_session)
    snapshot = await source.current_snapshot(
        run_id=sample_run["run_id"], source="canonical", approved_only=True,
    )

    projected = [item async for page in source.project(
        snapshot, ProfileRef(name="mhm-wikidata", version="1-nodes"),
    ) for item in page]

    result = next(item for item in projected if item.entity_key == "work:1")
    assert result.document["deferred_statements"] == [connection]
    assert result.document["statements"] == work["statements"][:1]
    assert result.local_references == ()

    publication = await _prepare_direct(
        db_session, sample_run["run_id"], sample_run["user_id"],
        target="live", profile_version="1-nodes",
    )
    page = await sample_run["client"].post(
        f"/api/runs/{sample_run['run_id']}/wikidata-publications/{publication.publication_id}/read",
        json={"query": {
            "type": "entities", "release_id": publication.current_release.release_id,
            "cursor": None, "limit": 50,
        }},
    )
    assert page.status_code == 200
    row = next(item for item in page.json()["items"] if item["entity_id"] == "work:1")
    assert row["deferred_statements"] == [connection]


async def _member_client(db_session, *, project_id, role):
    from httpx import ASGITransport, AsyncClient

    from app.auth import password as password_module
    from app.auth.session import COOKIE_NAME, create_session
    from app.crypto import index as index_module
    from app.crypto import kek as kek_module
    from app.crypto import pii
    from app.main import app
    from app.models.project import Membership
    from app.models.user import ROLE_EDITOR, User

    email = f"publication-viewer-{uuid.uuid4().hex[:8]}@example.com"
    password = "Correct-Horse-Battery-Staple-2!"  # noqa: S105
    user = User(
        email_index=index_module.blind_index(email),
        email_encrypted=pii.encrypt_pii(email),
        name_encrypted=pii.encrypt_pii("Publication Viewer"),
        password_hash=password_module.hash_password(password),
        kek_salt=pii.random_bytes(16),
        role=ROLE_EDITOR,
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(Membership(project_id=project_id, user_id=user.id, role=role))
    await db_session.commit()
    kek = kek_module.derive_kek(password, salt=user.kek_salt)
    session_row, session_secret = await create_session(db_session, user=user, kek=kek)
    await db_session.commit()
    cookie_value = (
        f"{session_row.id}."
        f"{base64.urlsafe_b64encode(session_secret).decode('ascii').rstrip('=')}"
    )
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    client.cookies.set(COOKIE_NAME, cookie_value)
    return client


@pytest.mark.asyncio
async def test_prepare_and_read_expose_the_nested_publication_contract(
    sample_run,
    db_session,
    monkeypatch,
) -> None:
    from app.models.run_job import RunJob
    from app.pipeline.wikidata_publication_prepare_job import run_wikidata_publication_prepare_job

    monkeypatch.setattr("app.pipeline.run_job_service.spawn_job", lambda _job_id: None)

    run_id = sample_run["run_id"]
    await _add_canonical_cache(db_session, run_id)
    prepared = await sample_run["client"].post(
            f"/api/runs/{run_id}/wikidata-publications/prepare",
            json={
                "profile_id": "mhm-wikidata",
                "profile_version": "1",
                "target": "live",
                "source": {
                    "kind": "run",
                    "projection_source": "canonical",
                    "approved_only": True,
                },
            },
        )

    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["publication"] is None
    prepare_job_id = prepared.json()["operation"]["operation_id"]
    await run_wikidata_publication_prepare_job(uuid.UUID(prepare_job_id))
    prepare_job = await db_session.get(RunJob, uuid.UUID(prepare_job_id))
    assert prepare_job is not None
    publication_id = str(prepare_job.result["publication_id"])
    summary = await sample_run["client"].post(
        f"/api/runs/{run_id}/wikidata-publications/{publication_id}/read",
        json={"query": {"type": "summary"}},
    )
    assert summary.status_code == 200, summary.text
    publication = summary.json()["publication"]
    assert publication["run_id"] == str(run_id)
    assert publication["target"] == "live"
    assert publication["status"] == "ready_for_review"
    assert publication["source_current"] is True
    assert publication["current_release"]["entity_count"] == 2
    assert publication["current_release"]["finding_counts"] == {
        "error": 0,
        "warning": 0,
        "info": 0,
    }

    release = publication["current_release"]
    page = await sample_run["client"].post(
        f"/api/runs/{run_id}/wikidata-publications/{publication['publication_id']}/read",
        json={
            "query": {
                "type": "entities",
                "release_id": release["release_id"],
                "cursor": None,
                "limit": 1,
            }
        },
    )

    assert page.status_code == 200, page.text
    assert page.json()["total"] == 2
    assert page.json()["items"][0]["entity_id"] == "work:1"
    assert page.json()["items"][0]["label"] == "Work 1"
    assert page.json()["next_cursor"]


@pytest.mark.asyncio
async def test_advance_binds_review_and_dry_run_but_never_writes_in_the_route(
    sample_run,
    db_session,
    monkeypatch,
) -> None:
    from sqlalchemy import select

    from app.main import app
    from app.models.publication import PublicationExecution
    from app.models.run_job import RunJob
    from app.publication.runtime import PublicationRuntime
    from app.routers.publication import get_publication_gateway_factory
    from app.schemas.publication import PreparePublicationRequest

    monkeypatch.setattr(
        "app.pipeline.run_job_service.spawn_job",
        lambda _job_id: None,
    )

    saved = await sample_run["client"].put("/api/me/api-keys/wikidata_test", json={"value": "Fixture@Test:publication-fixture"})
    assert saved.status_code == 200

    run_id = sample_run["run_id"]
    await _add_canonical_cache(db_session, run_id)
    gateway = FakeWikidataGateway(
        observations={
            "work:1": TargetObservation.absent("work:1"),
            "work:2": TargetObservation.absent("work:2"),
        }
    )

    def gateway_factory(*, target, actor_id):
        del target, actor_id
        return gateway

    app.dependency_overrides[get_publication_gateway_factory] = lambda: gateway_factory
    try:
        prepared = await PublicationRuntime(
            session=db_session,
            gateway_factory=gateway_factory,
        ).prepare(
            run_id=run_id,
            actor_id=str(sample_run["user_id"]),
            request=PreparePublicationRequest.model_validate(
                {
                    "profile_id": "mhm-wikidata",
                    "profile_version": "1",
                    "target": "test",
                    "source": {
                        "kind": "run",
                        "projection_source": "canonical",
                        "approved_only": True,
                    },
                }
            ),
        )
        assert prepared.publication is not None
        publication = prepared.publication.model_dump(mode="json")
        release = publication["current_release"]
        publication_url = (
            f"/api/runs/{run_id}/wikidata-publications/"
            f"{publication['publication_id']}"
        )
        reviewed = await sample_run["client"].post(
            f"{publication_url}/advance",
            json={
                "command": {
                    "type": "review",
                    "release_id": release["release_id"],
                    "expected_release_digest": release["release_digest"],
                    "selection": {"mode": "eligible_release"},
                    "decision": "approve",
                    "reason": "The curator approves the eligible Release.",
                }
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        approval = reviewed.json()["publication"]["approval_set"]
        assert approval["status"] == "approved"
        assert approval["pending_count"] == 0
        assert approval["approval_digest"]

        dry_run = await sample_run["client"].post(
            f"{publication_url}/advance",
            json={
                "command": {
                    "type": "dry_run",
                    "approval_set_id": approval["approval_set_id"],
                    "expected_approval_digest": approval["approval_digest"],
                }
            },
        )
        assert dry_run.status_code == 200, dry_run.text
        assert dry_run.json()["operation"]["status"] == "queued"
        from app.pipeline.wikidata_publication_dry_run_job import run_wikidata_publication_dry_run_job
        monkeypatch.setattr("app.pipeline.wikidata_publication_dry_run_job.WikidataGatewayAdapter", lambda **kwargs: gateway)
        await run_wikidata_publication_dry_run_job(uuid.UUID(dry_run.json()["operation"]["operation_id"]))
        dry_summary = await sample_run["client"].post(f"{publication_url}/read", json={"query": {"type": "summary"}})
        dry_run_publication = dry_summary.json()["publication"]
        assert dry_run_publication["plan"]["action_counts"]["create"] == 2
        assert dry_run_publication["dry_run_receipt"]["status"] == "valid"

        publish = await sample_run["client"].post(
            f"{publication_url}/advance",
            json={
                "command": {
                    "type": "publish",
                    "plan_id": dry_run_publication["plan"]["plan_id"],
                    "dry_run_receipt_id": (
                        dry_run_publication["dry_run_receipt"]["dry_run_receipt_id"]
                    ),
                    "expected_receipt_digest": (
                        dry_run_publication["dry_run_receipt"]["receipt_digest"]
                    ),
                }
            },
        )
        assert publish.status_code == 200, publish.text
        publish_body = publish.json()
        operation = publish_body["operation"]
        assert operation["command"] == "publish"
        assert operation["status"] == "queued"
        assert publish_body["publication"]["execution"]["status"] == "queued"
        assert gateway.write_calls == ()

        operation_read = await sample_run["client"].post(
            f"{publication_url}/read",
            json={
                "query": {
                    "type": "operation",
                    "operation_id": operation["operation_id"],
                }
            },
        )
        assert operation_read.status_code == 200, operation_read.text
        assert operation_read.json()["operation"]["status"] == "queued"

        execution = (
            await db_session.execute(
                select(PublicationExecution).where(
                    PublicationExecution.id == uuid.UUID(operation["operation_id"])
                )
            )
        ).scalar_one()
        execution.status = "paused"
        await db_session.commit()
        paused_operation = await sample_run["client"].post(
            f"{publication_url}/read",
            json={
                "query": {
                    "type": "operation",
                    "operation_id": operation["operation_id"],
                }
            },
        )
        assert paused_operation.status_code == 200, paused_operation.text
        assert paused_operation.json()["operation"]["status"] == "succeeded"
        assert paused_operation.json()["operation"]["progress"]["status"] == "paused"

        resumed = await sample_run["client"].post(
            f"{publication_url}/advance",
            json={
                "command": {
                    "type": "resume",
                    "execution_id": operation["operation_id"],
                }
            },
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["operation"]["command"] == "resume"
        assert resumed.json()["operation"]["status"] == "queued"
        execution_jobs = (
            await db_session.execute(
                select(RunJob).where(
                    RunJob.kind == "wikidata_publication_execution",
                )
            )
        ).scalars().all()
        assert len(execution_jobs) == 1
        from app.publication.credentials import ExecutionCredentialResolver
        job = execution_jobs[0]
        envelope = job.params["_publication_credential"]
        assert "publication-fixture" not in envelope
        material = await ExecutionCredentialResolver(envelope,
            publication_id=publication["publication_id"], execution_id=operation["operation_id"],
            actor_id=str(sample_run["user_id"])).resolve(f"wikidata:test:{sample_run['user_id']}")
        assert material.secret == "Fixture@Test:publication-fixture"
        job_view = await sample_run["client"].get(f"/api/runs/{run_id}/jobs/{job.id}")
        assert job_view.status_code == 200
        assert "_publication_credential" not in job_view.json()["params"]
        assert "publication-fixture" not in job_view.text

        assert all(
            job.params["execution_id"] == operation["operation_id"]
            for job in execution_jobs
        )
        assert gateway.write_calls == ()

        cancelled = await sample_run["client"].post(
            f"{publication_url}/advance",
            json={
                "command": {
                    "type": "cancel",
                    "operation_id": operation["operation_id"],
                    "reason": "The curator stops this Execution.",
                }
            },
        )
    finally:
        app.dependency_overrides.pop(get_publication_gateway_factory, None)

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["publication"]["status"] == "cancelled"
    assert cancelled.json()["operation"]["status"] == "cancelled"
    assert gateway.write_calls == ()


@pytest.mark.asyncio
async def test_live_prepare_requires_canonical_and_summary_uses_backend_currency(
    sample_run,
    db_session,
) -> None:
    from sqlalchemy import select

    run_id = sample_run["run_id"]
    client = sample_run["client"]
    rejected = await client.post(
        f"/api/runs/{run_id}/wikidata-publications/prepare",
        json={
            "profile_id": "mhm-wikidata",
            "profile_version": "1",
            "target": "live",
            "source": {
                "kind": "run",
                "projection_source": "legacy",
                "approved_only": True,
            },
        },
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "Live publication requires the canonical source"

    await _add_canonical_cache(db_session, run_id)
    publication = await _prepare_direct(
        db_session, run_id, sample_run["user_id"], target="live"
    )
    publication_id = publication.publication_id
    cache = (
        await db_session.execute(
            select(WikidataStudioCache).where(
                WikidataStudioCache.run_id == run_id,
                WikidataStudioCache.source == "canonical",
                WikidataStudioCache.approved_only.is_(True),
            )
        )
    ).scalar_one()
    cache.input_fingerprint = "b" * 64
    await db_session.commit()

    summary = await client.post(
        f"/api/runs/{run_id}/wikidata-publications/{publication_id}/read",
        json={"query": {"type": "summary"}},
    )
    assert summary.status_code == 200, summary.text
    assert summary.json()["publication"]["source_current"] is False


@pytest.mark.asyncio
async def test_viewers_can_read_but_only_editors_can_mutate(
    sample_run,
    db_session,
) -> None:
    from app.models.project import PROJECT_ROLE_VIEWER

    run_id = sample_run["run_id"]
    await _add_canonical_cache(db_session, run_id)
    publication = (await _prepare_direct(
        db_session, run_id, sample_run["user_id"], target="test"
    )).model_dump(mode="json")
    viewer = await _member_client(
        db_session,
        project_id=sample_run["project_id"],
        role=PROJECT_ROLE_VIEWER,
    )
    try:
        read = await viewer.post(
            f"/api/runs/{run_id}/wikidata-publications/{publication['publication_id']}/read",
            json={"query": {"type": "summary"}},
        )
        forbidden_prepare = await viewer.post(
            f"/api/runs/{run_id}/wikidata-publications/prepare",
            json={
                "profile_id": "mhm-wikidata",
                "profile_version": "1",
                "target": "test",
                "source": {
                    "kind": "run",
                    "projection_source": "canonical",
                    "approved_only": True,
                },
            },
        )
        forbidden_advance = await viewer.post(
            f"/api/runs/{run_id}/wikidata-publications/{publication['publication_id']}/advance",
            json={
                "command": {
                    "type": "review",
                    "release_id": publication["current_release"]["release_id"],
                    "expected_release_digest": publication["current_release"]["release_digest"],
                    "selection": {"mode": "eligible_release"},
                    "decision": "approve",
                    "reason": "A viewer cannot approve this Release.",
                }
            },
        )
    finally:
        await viewer.aclose()

    assert read.status_code == 200
    assert forbidden_prepare.status_code == 403
    assert forbidden_advance.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize('target,key_name,environment', [('live','wikidata','production'), ('test','wikidata_test','test')])
@pytest.mark.parametrize("outcome", ["success", "read_error", "blocked", "cancelled", "stale"])
async def test_saved_credentials_reach_dry_run_and_background_execution(sample_run, db_session, monkeypatch, target, key_name, environment, outcome):
    from sqlalchemy import select
    from app.models.run_job import RunJob
    from app.pipeline.wikidata_publication_execution_job import run_wikidata_publication_execution_job
    from app.publication.wikidata_gateway import MutationConfirmation

    opened = []
    writes = []

    class Boundary:
        async def reconcile_batch(self, entities):
            if outcome == "read_error":
                raise RuntimeError("The Wikidata read did not return a result")
            if outcome == "blocked":
                return tuple(TargetObservation.unknown(entity.entity_key, "Lookup is inconclusive") for entity in entities)
            return tuple(TargetObservation.absent(entity.entity_key) for entity in entities)

        async def write_once(self, request):
            writes.append(request)
            return f'Q{9000 + len(writes)}'

        async def confirm_mutation(self, mutation, *, qid):
            return MutationConfirmation.applied(qid=qid, revision=1, fingerprint='confirmed')

    async def open_boundary(self, material):
        opened.append(material)
        return Boundary()

    monkeypatch.setattr('app.publication.wikidata_gateway.CurrentWikidataBoundaryFactory.open', open_boundary)
    monkeypatch.setattr('app.pipeline.run_job_service.spawn_job', lambda job_id: None)
    client = sample_run['client']
    token = f'Fixture@{key_name}:saved-fixture'
    assert (await client.put(f'/api/me/api-keys/{key_name}', json={'value': token})).status_code == 200
    await _add_canonical_cache(db_session, sample_run['run_id'])
    publication = await _prepare_direct(db_session, sample_run['run_id'], sample_run['user_id'], target=target)
    url = f"/api/runs/{sample_run['run_id']}/wikidata-publications/{publication.publication_id}/advance"
    release = publication.current_release
    reviewed = await client.post(url, json={'command': {
        'type':'review', 'release_id':release.release_id, 'expected_release_digest':release.release_digest,
        'selection':{'mode':'eligible_release'}, 'decision':'approve', 'reason':'Reviewed fixture',
    }})
    assert reviewed.status_code == 200, reviewed.text
    approval = reviewed.json()['publication']['approval_set']
    dry = await client.post(url, json={'command': {'type':'dry_run',
        'approval_set_id':approval['approval_set_id'], 'expected_approval_digest': '0' * 64 if outcome == 'stale' else approval['approval_digest']}})
    assert dry.status_code == 200, dry.text
    assert dry.json()['operation']['status'] == 'queued'
    assert opened == []
    assert writes == []
    from app.pipeline.wikidata_publication_dry_run_job import run_wikidata_publication_dry_run_job
    job_id = uuid.UUID(dry.json()['operation']['operation_id'])
    if outcome == "cancelled":
        cancelled = await client.post(f'/api/runs/{sample_run["run_id"]}/jobs/{job_id}/cancel', json={})
        assert cancelled.status_code == 200
    from app.pipeline.run_job_service import _execute_job
    await _execute_job(job_id)
    job_result = await client.get(f'/api/runs/{sample_run["run_id"]}/jobs/{job_id}')
    if outcome in {"success", "blocked"}:
        latest = await client.get(f'/api/runs/{sample_run["run_id"]}/wikidata-publications/latest')
        assert latest.status_code == 200, latest.text
        last_state = latest.json()['publication']
        assert last_state['publication_id'] == publication.publication_id
        assert last_state['dry_run_receipt'] is not None
        opened_before = len(opened)
        retry = await client.post(url, json={'command': {'type': 'dry_run',
            'approval_set_id': approval['approval_set_id'],
            'expected_approval_digest': approval['approval_digest']}})
        assert retry.status_code == 200, retry.text
        assert retry.json()['publication']['plan']['plan_id'] == last_state['plan']['plan_id']
        assert retry.json()['operation'] is None
        assert len(opened) == opened_before
        if outcome == "blocked":
            assert len(last_state['plan']['blocked_actions']) == 2
        from datetime import datetime, timedelta
        from app.routers import publication as publication_router

        class FutureClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) + timedelta(hours=2)

        with monkeypatch.context() as clock_patch:
            clock_patch.setattr(publication_router, "datetime", FutureClock)
            expired = await client.post(url, json={'command': {'type': 'dry_run',
                'approval_set_id': approval['approval_set_id'],
                'expected_approval_digest': approval['approval_digest']}})
        assert expired.status_code == 200, expired.text
        assert expired.json()['operation']['status'] == 'queued'
        await _execute_job(uuid.UUID(expired.json()['operation']['operation_id']))
        forced = await client.post(url, json={'command': {'type': 'dry_run',
            'approval_set_id': approval['approval_set_id'],
            'expected_approval_digest': approval['approval_digest'], 'force_refresh': True}})
        assert forced.status_code == 200, forced.text
        assert forced.json()['operation']['status'] == 'queued'
        await _execute_job(uuid.UUID(forced.json()['operation']['operation_id']))
        assert len(opened) > opened_before
    if outcome != "success":
        assert job_result.json()['status'] == ("cancelled" if outcome == "cancelled" else "failed"), job_result.text
        assert writes == []
        assert token not in job_result.text
        if outcome == "read_error":
            assert "The Wikidata read did not return a result" in job_result.json()['error']
        return
    assert job_result.json()['status'] == 'succeeded', job_result.text
    assert token not in job_result.text
    summary = await client.post(url.replace('/advance', '/read'), json={'query': {'type': 'summary'}})
    state = summary.json()['publication']
    assert state['dry_run_receipt']['status'] == 'valid'
    assert writes == []
    assert opened[-1].secret == token
    assert opened[-1].target.environment == environment
    receipt = state['dry_run_receipt']
    published = await client.post(url, json={'command': {'type':'publish', 'plan_id':state['plan']['plan_id'],
        'dry_run_receipt_id':receipt['dry_run_receipt_id'], 'expected_receipt_digest':receipt['receipt_digest']}})
    assert published.status_code == 200, published.text
    assert writes == []
    jobs = (await db_session.execute(select(RunJob).where(RunJob.kind=='wikidata_publication_execution'))).scalars().all()
    assert len(jobs) == 1
    assert token not in str(jobs[0].params)
    await run_wikidata_publication_execution_job(jobs[0].id)
    await db_session.refresh(jobs[0])
    audit = await client.post(url.replace("/advance", "/read"), json={"query": {"type": "audit", "limit": 50}})
    assert jobs[0].status == "succeeded", audit.text
    assert len(writes) == 2
    assert all(material.secret == token and material.target.environment == environment for material in opened)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["full", "partial", "error", "changed"])
async def test_ai_review_report_is_durable_and_only_recommends_supported_consents(sample_run, db_session, monkeypatch, outcome):
    import json
    from app.pipeline.agent_runner import AgentEvent
    from app.pipeline.wikidata_publication_dry_run_job import run_wikidata_publication_dry_run_job
    from app.publication.wikidata_gateway import RemoteEntitySnapshot

    review_mode = False
    judge_calls = []

    class Boundary:
        async def reconcile_batch(self, entities):
            return tuple(
                TargetObservation.present_foreign(e.entity_key, qid="Q123", remote_revision=7, fingerprint="remote")
                if e.entity_key == "work:1" else TargetObservation.absent(e.entity_key) if review_mode
                else TargetObservation.unknown(e.entity_key, "Lookup timeout") for e in entities
            )

        async def fetch_entity(self, qid):
            return RemoteEntitySnapshot(qid=qid, revision=8 if outcome == "changed" else 7,
                fingerprint="remote", document={"id": qid, "labels": {"en": {"value": "Work 1"}}, "claims": {}})

        async def write_once(self, request):
            pytest.fail("AI review must never write to Wikidata")

    async def open_boundary(self, material):
        return Boundary()

    async def fake_subprocess(**kwargs):
        rows = json.loads((kwargs["pipeline_output"] / "wikidata_items.json").read_text())
        assert rows[0]["publication_review"]["remote_entity"]["id"] == "Q123"
        assert rows[0]["publication_review"]["proposed_entity"]["statements"]
        judge_calls.append(rows)
        if outcome == "error":
            raise RuntimeError("Judge unavailable")
        root = kwargs["state_dir"] / "runs" / "fixture"
        root.mkdir(parents=True)
        (root / "results.jsonl").write_text(json.dumps({
            "record_id": "work:1", "evaluator_id": "wikidata_publication_review", "error": None,
            "verification_status": "judged", "verdict": {"overall": outcome,
            "name_ok": "yes", "type_ok": "yes", "reasoning": "The identifiers and proposed claims agree."},
        }) + "\n")
        yield AgentEvent(type="runner.exit", payload={"return_code": 0})

    monkeypatch.setenv("GEMINI_API_KEY", "fixture-ai-key")
    monkeypatch.setattr("app.pipeline.run_job_service.spawn_job", lambda _job_id: None)
    monkeypatch.setattr("app.publication.wikidata_gateway.CurrentWikidataBoundaryFactory.open", open_boundary)
    monkeypatch.setattr("app.pipeline.agent_runner.spawn_eval_agent_run", fake_subprocess)
    client = sample_run["client"]
    assert (await client.put("/api/me/api-keys/wikidata_test", json={"value": "Fixture@Test:publication-fixture"})).status_code == 200
    run_id = sample_run["run_id"]
    await _add_canonical_cache(db_session, run_id)
    publication = await _prepare_direct(db_session, run_id, sample_run["user_id"], target="test")
    url = f"/api/runs/{run_id}/wikidata-publications/{publication.publication_id}"
    release = publication.current_release
    reviewed = await client.post(f"{url}/advance", json={"command": {
        "type": "review", "release_id": release.release_id, "expected_release_digest": release.release_digest,
        "selection": {"mode": "eligible_release"}, "decision": "approve", "reason": "Reviewed fixture",
    }})
    approval = reviewed.json()["publication"]["approval_set"]
    queued = await client.post(f"{url}/advance", json={"command": {"type": "dry_run",
        "approval_set_id": approval["approval_set_id"], "expected_approval_digest": approval["approval_digest"]}})
    await run_wikidata_publication_dry_run_job(uuid.UUID(queued.json()["operation"]["operation_id"]))
    summary = (await client.post(f"{url}/read", json={"query": {"type": "summary"}})).json()["publication"]
    plan = summary["plan"]
    review_mode = True
    request = {"plan_id": plan["plan_id"], "plan_digest": plan["plan_digest"], "tier_model": "gemini-3.5-flash"}
    started = await client.post(f"{url}/ai-review", json=request)
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "queued"
    assert judge_calls == []
    from app.pipeline.wikidata_publication_ai_review_job import run_wikidata_publication_ai_review_job
    await run_wikidata_publication_ai_review_job(uuid.UUID(started.json()["job_id"]))
    restored = await client.get(f"{url}/ai-review")
    assert restored.status_code == 200, restored.text
    report = restored.json()
    assert report["status"] == "succeeded"
    assert report["processed"] == report["total"] == 2
    assert report["report"]["plan_digest"] == plan["plan_digest"]
    items = report["report"]["items"]
    assert items[0]["status"] == ("recommended" if outcome == "full" else "error" if outcome == "error" else "review_required")
    assert (items[0]["consent"] is not None) == (outcome == "full")
    assert items[1]["status"] == "lookup_resolved"
    assert items[1]["consent"] is None
    cached = await client.post(f"{url}/ai-review", json=request)
    assert cached.json()["job_id"] == report["job_id"]
    stale = await client.post(f"{url}/ai-review", json={**request, "plan_digest": "outdated"})
    assert stale.status_code == 409
    unchanged = (await client.post(f"{url}/read", json={"query": {"type": "summary"}})).json()["publication"]
    assert unchanged["dry_run_receipt"]["status"] == "failed"
    assert unchanged["execution"] is None
    raw_job = await client.get(f"/api/runs/{run_id}/jobs/{report['job_id']}")
    assert "fixture-ai-key" not in raw_job.text
    assert "_ai_credential" not in raw_job.text
    assert "report" not in (raw_job.json()["result"] or {})
    # SQLite timestamps have one-second resolution; separate the two job dates.
    import asyncio
    await asyncio.sleep(1.05)
    fresh = await client.post(f"{url}/ai-review", json={**request, "force_refresh": True})
    assert fresh.json()["job_id"] != report["job_id"]
    assert (await client.post(f"/api/runs/{run_id}/jobs/{fresh.json()['job_id']}/cancel")).status_code == 200
    await run_wikidata_publication_ai_review_job(uuid.UUID(fresh.json()["job_id"]))
    cancelled = await client.get(f"{url}/ai-review")
    assert cancelled.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_publication_ai_review_requires_editor_access(sample_run, db_session):
    from app.models.project import PROJECT_ROLE_VIEWER
    await _add_canonical_cache(db_session, sample_run['run_id'])
    publication = await _prepare_direct(db_session, sample_run['run_id'], sample_run['user_id'], target='test')
    viewer = await _member_client(db_session, project_id=sample_run['project_id'], role=PROJECT_ROLE_VIEWER)
    url = f"/api/runs/{sample_run['run_id']}/wikidata-publications/{publication.publication_id}/ai-review"
    try:
        response = await viewer.post(url, json={'plan_id': 'plan', 'plan_digest': 'digest'})
        assert response.status_code == 403
        read = await viewer.get(url)
        assert read.status_code == 200
        assert read.json()['report'] is None
    finally:
        await viewer.aclose()


@pytest.mark.asyncio
async def test_prepare_reference_only_resolves_connections_and_preserves_source(sample_run, db_session):
    from app.publication.runtime import PublicationRuntime
    from app.schemas.publication import PreparePublicationRequest, AdvancePublicationRequest, PublicationEntitiesQuery

    work = _canonical_item('work:1', 'Work')
    work['statements'].append({'property': 'P50', 'value': '__LOCAL:person:1', 'value_type': 'item'})
    person = _canonical_item('person:1', 'Author')
    person['entity_type'] = 'person'
    await _add_canonical_cache(db_session, sample_run['run_id'], items=[work, person])
    gateway = FakeWikidataGateway(observations={
        'person:1': TargetObservation.present_foreign('person:1', qid='Q123', remote_revision=7),
        'work:1': TargetObservation.absent('work:1'),
    })
    runtime = PublicationRuntime(session=db_session, gateway_factory=lambda **kw: gateway)
    scope = {'run_id': sample_run['run_id'], 'actor_id': str(sample_run['user_id'])}
    request = {'profile_id': 'mhm-wikidata', 'profile_version': '1-nodes', 'target': 'test',
        'source': {'kind': 'run', 'projection_source': 'canonical', 'approved_only': True}}
    original = (await runtime.prepare(**scope, request=PreparePublicationRequest.model_validate(request))).publication

    async def check(publication):
        release = publication.current_release
        reviewed = (await runtime.advance(**scope, publication_id=publication.publication_id,
            request=AdvancePublicationRequest.model_validate({'command': {'type': 'review',
                'release_id': release.release_id, 'expected_release_digest': release.release_digest,
                'selection': {'mode': 'eligible_release'}, 'decision': 'approve', 'reason': 'Reviewed identity'}}))).publication
        approval = reviewed.approval_set
        return (await runtime.advance(**scope, publication_id=publication.publication_id,
            request=AdvancePublicationRequest.model_validate({'command': {'type': 'dry_run',
                'approval_set_id': approval.approval_set_id, 'expected_approval_digest': approval.approval_digest}}))).publication

    original = await check(original)
    request['reference_only'] = {'publication_id': original.publication_id,
        'plan_id': original.plan.plan_id, 'plan_digest': original.plan.plan_digest, 'entity_keys': ['person:1']}
    replacement = (await runtime.prepare(**scope, request=PreparePublicationRequest.model_validate(request))).publication
    assert replacement.current_release.release_digest != original.current_release.release_digest
    assert replacement.approval_set is None
    page = await runtime.read(**scope, publication_id=replacement.publication_id,
        query=PublicationEntitiesQuery(type='entities', release_id=replacement.current_release.release_id))
    author, work_row = page.items
    assert author.reference_only is True
    assert author.target_qid == 'Q123'
    assert author.proposed_action == 'skip'
    assert work_row.statement_count == 2
    assert work_row.deferred_statements == []
    replacement = await check(replacement)
    assert replacement.plan.action_counts == {'create': 1, 'update': 0, 'skip': 1, 'blocked': 0}
    assert replacement.dry_run_receipt.status == 'valid'
    assert not gateway.write_calls
    # A stale Plan cannot establish a new identity choice.
    request['reference_only']['plan_digest'] = '0' * 64
    with pytest.raises(ValueError, match='current Plan'):
        await runtime.prepare(**scope, request=PreparePublicationRequest.model_validate(request))

    request['reference_only']['plan_digest'] = original.plan.plan_digest
    for keys in (['work:1'], ['missing'], ['person:1', 'person:1']):
        request['reference_only']['entity_keys'] = keys
        with pytest.raises(ValueError):
            await runtime.prepare(**scope, request=PreparePublicationRequest.model_validate(request))
    request['reference_only']['entity_keys'] = ['person:1']
    request['target'] = 'live'
    with pytest.raises(ValueError, match='current Plan'):
        await runtime.prepare(**scope, request=PreparePublicationRequest.model_validate(request))

    receipt = replacement.dry_run_receipt
    queued = (await runtime.advance(**scope, publication_id=replacement.publication_id,
        request=AdvancePublicationRequest.model_validate({'command': {'type': 'publish',
            'plan_id': replacement.plan.plan_id, 'dry_run_receipt_id': receipt.dry_run_receipt_id,
            'expected_receipt_digest': receipt.receipt_digest}}))).publication
    completed = await runtime.execute(**scope, publication_id=replacement.publication_id,
        execution_id=queued.execution.execution_id, worker_id='fixture-worker')
    assert completed.execution.status == 'succeeded'
    assert completed.execution.total == 1
    assert [call.mutation.entity_key for call in gateway.write_calls] == ['work:1']
    assert gateway.write_calls[0].mutation.document['statements'][-1]['value'] == 'Q123'


@pytest.mark.asyncio
@pytest.mark.parametrize('alternative', [False, True])
async def test_automatic_resolution_creates_an_approved_subset_without_writes(sample_run, db_session, monkeypatch, alternative):
    import json
    import httpx
    from app.pipeline.agent_runner import AgentEvent
    from app.pipeline.wikidata_publication_dry_run_job import run_wikidata_publication_dry_run_job
    from app.pipeline.wikidata_publication_ai_review_job import run_wikidata_publication_ai_review_job
    from app.publication.wikidata_gateway import RemoteEntitySnapshot
    calls = []
    monkeypatch.setenv('GEMINI_API_KEY', 'fixture-ai-key')
    class Boundary:
        async def reconcile_batch(self, entities):
            return tuple(TargetObservation.present_foreign(e.entity_key, qid='Q456' if e.document.get('existing_qid') == 'Q456' else 'Q123', remote_revision=7)
                if e.entity_key == 'work:1' else TargetObservation.unknown(e.entity_key, 'Unavailable')
                if e.entity_key == 'work:4' else TargetObservation.absent(e.entity_key) for e in entities)
        async def fetch_entity(self, qid):
            return RemoteEntitySnapshot(qid=qid, revision=7, fingerprint='remote', document={'id': qid,
                'claims': {'P8189': [{'mainsnak': {'datavalue': {'value': '123'}}}]}})
        async def write_once(self, request):
            pytest.fail('Automatic resolution must not write to Wikidata')
    async def open_boundary(self, credential):
        return Boundary()
    async def subprocess(**kwargs):
        item = json.loads((kwargs['pipeline_output'] / 'wikidata_items.json').read_text())[0]
        pack = item['publication_review']
        assert pack['automatic'] is True
        calls.append((item['local_id'], pack['check']))
        decision = {'identity': 'same_entity' if pack['qid'] else 'new_entity',
            'labels_supported': True, 'identity_evidence': [pack['evidence'][0]['id']],
            'reason': 'Catalogue supports the subject.', 'claims': [
                {'index': i, 'status': 'supported', 'evidence': [pack['evidence'][0]['id']]}
                for i, _ in enumerate(pack['proposed_entity']['statements'])]}
        if item['local_id'] == 'work:3': decision['identity'] = 'unresolved'
        if alternative and item['local_id'] == 'work:1' and pack['qid'] == 'Q123':
            decision['identity'] = 'different_entity'
        root = kwargs['state_dir'] / 'runs' / 'fixture'
        root.mkdir(parents=True)
        (root / 'results.jsonl').write_text(json.dumps({'record_id': item['local_id'],
            'evaluator_id': 'wikidata_publication_review', 'verification_status': 'judged', 'error': None,
            'verdict': {'overall': 'partial', 'name_ok': 'yes', 'type_ok': 'yes', 'reasoning': 'Identity separate from claims.',
                'publication_decision': None if len(calls) == 1 and not alternative else decision}}))
        yield AgentEvent(type='runner.exit', payload={'return_code': 0})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(**kwargs,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'search': [{'id': 'Q456'}]})
            if request.url.params.get('action') == 'wbsearchentities' else httpx.Response(200, text='Primary catalogue: identifier 123. Works and authors.'))))
    monkeypatch.setattr('app.pipeline.run_job_service.spawn_job', lambda _job_id: None)
    monkeypatch.setattr('app.publication.wikidata_gateway.CurrentWikidataBoundaryFactory.open', open_boundary)
    monkeypatch.setattr('app.pipeline.agent_runner.spawn_eval_agent_run', subprocess)
    items = [_canonical_item(f'work:{i}', f'Work {i}') for i in range(1, 5)]
    for item in items:
        item['statements'][0]['references'] = [{'property': 'P854', 'value': 'https://www.nli.org.il/fixture'}]
    items[0]['statements'].append({'property': 'P8189', 'value': '123'})
    items[1]['statements'].append({'property': 'P50', 'value': '__LOCAL:work:3'})
    await _add_canonical_cache(db_session, sample_run['run_id'], items=items)
    client, run_id = sample_run['client'], sample_run['run_id']
    assert (await client.put('/api/me/api-keys/wikidata_test', json={'value': 'Fixture@Test:publication-fixture'})).status_code == 200
    publication = await _prepare_direct(db_session, run_id, sample_run['user_id'], target='test', profile_version='1-nodes')
    url = f'/api/runs/{run_id}/wikidata-publications/{publication.publication_id}'
    reviewed = (await client.post(url + '/advance', json={'command': {'type': 'review',
        'release_id': publication.current_release.release_id, 'expected_release_digest': publication.current_release.release_digest,
        'selection': {'mode': 'eligible_release'}, 'decision': 'approve', 'reason': 'Initial fixture review'}})).json()['publication']
    approval = reviewed['approval_set']
    queued = await client.post(url + '/advance', json={'command': {'type': 'dry_run',
        'approval_set_id': approval['approval_set_id'], 'expected_approval_digest': approval['approval_digest']}})
    await run_wikidata_publication_dry_run_job(uuid.UUID(queued.json()['operation']['operation_id']))
    current = (await client.post(url + '/read', json={'query': {'type': 'summary'}})).json()['publication']
    started = await client.post(url + '/ai-review', json={'plan_id': current['plan']['plan_id'],
        'plan_digest': current['plan']['plan_digest'], 'tier_model': 'gemini-3.5-flash', 'automatic': True})
    assert started.status_code == 200, started.text
    assert not calls
    await run_wikidata_publication_ai_review_job(uuid.UUID(started.json()['job_id']))
    saved = (await client.get(url + '/ai-review')).json()
    assert saved['status'] == 'succeeded', saved
    report = saved['report']
    assert [row['status'] for row in report['items']] == ['reuse_existing', 'create', 'deferred', 'deferred']
    assert all(row['consent'] is None for row in report['items'])
    assert report['items'][0]['qid'] == ('Q456' if alternative else 'Q123')
    assert len(calls) == (8 if alternative else 7)
    new_url = f"/api/runs/{run_id}/wikidata-publications/{report['result_publication_id']}"
    result = (await client.post(new_url + '/read', json={'query': {'type': 'summary'}})).json()['publication']
    assert result['current_release']['entity_count'] == 2
    assert result['approval_set']['approved_count'] == 2
    assert result['plan']['action_counts'] == {'create': 1, 'update': 0, 'skip': 1, 'blocked': 0}
    assert result['dry_run_receipt']['status'] == 'valid'
    assert result['execution'] is None
    page = (await client.post(new_url + '/read', json={'query': {'type': 'entities', 'release_id': result['current_release']['release_id']}})).json()
    assert page['items'][1]['deferred_statements'][0]['value'] == '__LOCAL:work:3'

    # Retry only the unavailable lookup; retain the six completed model checks.
    import asyncio
    await asyncio.sleep(1.05)
    retry = await client.post(url + '/ai-review', json={'plan_id': current['plan']['plan_id'],
        'plan_digest': current['plan']['plan_digest'], 'tier_model': 'gemini-3.5-flash', 'automatic': True})
    assert retry.json()['job_id'] != started.json()['job_id']
    await run_wikidata_publication_ai_review_job(uuid.UUID(retry.json()['job_id']))
    restored = (await client.get(url + '/ai-review')).json()
    assert restored['status'] == 'succeeded', restored
    assert restored['processed'] == 4
    assert len(calls) == (8 if alternative else 7)
    await asyncio.sleep(1.05)
    fresh = await client.post(url + '/ai-review', json={'plan_id': current['plan']['plan_id'],
        'plan_digest': current['plan']['plan_digest'], 'tier_model': 'gemini-3.5-flash', 'automatic': True, 'force_refresh': True})
    assert fresh.json()['job_id'] != retry.json()['job_id']
    assert (await client.post(f"/api/runs/{run_id}/jobs/{fresh.json()['job_id']}/cancel")).status_code == 200
    await run_wikidata_publication_ai_review_job(uuid.UUID(fresh.json()['job_id']))
    assert (await client.get(url + '/ai-review')).json()['status'] == 'cancelled'
    assert len(calls) == (8 if alternative else 7)


@pytest.mark.asyncio
async def test_automatic_evidence_uses_only_the_items_source_records(sample_run, db_session):
    from app.publication.automatic_evidence import collect_evidence
    own = await collect_evidence(db_session, {'record_ids': [sample_run['control_number']]},
        sample_run['user_id'], run_id=sample_run['run_id'])
    assert any(row['id'] == 'marc:' + sample_run['control_number'] for row in own)
    unrelated = await collect_evidence(db_session, {'record_ids': ['missing-record']},
        sample_run['user_id'], run_id=sample_run['run_id'])
    assert unrelated == []
