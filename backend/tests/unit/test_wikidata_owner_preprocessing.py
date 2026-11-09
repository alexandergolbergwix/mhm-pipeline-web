from types import SimpleNamespace

import pytest
from wikibaseintegrator import WikibaseIntegrator
from wikibaseintegrator.wbi_enums import ActionIfExists

from app.pipeline import wikidata_studio, wikidata_upload
from app.routers.wikidata_studio import studio_dict_to_native_item
from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.uploader import ClaimBuildError, WikidataUploader


def _preprocessed_item(statements):
    source = WikidataItem(
        local_id="QDraft_MS_10001",
        entity_type="manuscript",
        existing_qid="Q123",
        records=["10001"],
        labels={"en": "Manuscript 10001"},
        statements=[
            WikidataStatement("P31", "Q87167", "item"),
            WikidataStatement("P3959", "10001", "external-id"),
            *statements,
        ],
    )
    result = wikidata_studio.finish_native_items([source])
    return studio_dict_to_native_item(result["items"][0], strict_p3959_rank=True)


def _uploader_with_real_wbi_claims(monkeypatch, remote_statements):
    uploader = WikidataUploader(token="fixture", allow_live=True)
    wbi = WikibaseIntegrator()
    entity = wbi.item.new()
    entity.id = "Q123"
    entity.lastrevid = 42
    for index, statement in enumerate(remote_statements):
        claim = uploader._build_claim(statement)
        assert claim is not None
        claim.id = f"Q123$00000000-0000-0000-0000-{index + 1:012d}"
        entity.claims.add(claim, action_if_exists=ActionIfExists.FORCE_APPEND)
    writes = []
    entity.write = lambda **kwargs: writes.append(kwargs) or entity
    monkeypatch.setattr(
        uploader,
        "_init_wbi",
        lambda: SimpleNamespace(
            item=SimpleNamespace(get=lambda *args, **kwargs: entity),
        ),
    )
    monkeypatch.setattr(uploader, "owner_only_target_valid", lambda qid, cn: True)
    monkeypatch.setattr(uploader, "_owner_only_revision", lambda qid, cn: 42)
    monkeypatch.setattr(uploader, "_is_our_item", lambda *args, **kwargs: True)
    monkeypatch.setattr(uploader, "_assert_modifiable", lambda *args, **kwargs: None)
    monkeypatch.setattr(uploader, "_bot_excluded", lambda qid: False)
    monkeypatch.setattr(uploader, "_rate_limit", lambda: None)
    uploader._is_our_item_cache["Q123"] = True
    return uploader, entity, writes


def _remote_statements():
    return [
        WikidataStatement("P31", "Q87167", "item"),
        WikidataStatement("P3959", "10001", "external-id"),
        WikidataStatement("P11603", "Q888", "item"),
        WikidataStatement("P1574", "Q777", "item"),
    ]


@pytest.mark.asyncio
async def test_owner_native_builder_keeps_unresolved_cached_claim(monkeypatch):
    import uuid

    from app.routers import wikidata_studio as router

    cached = SimpleNamespace(
        input_fingerprint="f" * 64, summary={},
        result_items=[
            {
                "local_id": "QDraft_MS_10001",
                "entity_type": "manuscript",
                "records": ["10001"],
                "existing_qid": "Q123",
                "labels": {"en": "Manuscript"},
                "statements": [
                    {
                        "property_id": "P3959",
                        "value": "10001",
                        "value_type": "external-id",
                        "rank": "normal",
                    },
                    {
                        "property_id": "P11603",
                        "value": "__LOCAL:missing-person",
                        "value_type": "item",
                    },
                ],
            }
        ]
    )

    class DB:
        async def execute(self, query):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    async def lookup(*args, **kwargs):
        return None

    async def get_cache(*args, **kwargs):
        return cached

    async def current(*args, **kwargs):
        return True

    monkeypatch.setattr(router, "_lookup_run_with_access", lookup)
    monkeypatch.setattr(router, "_get_studio_cache_row", get_cache)
    monkeypatch.setattr(router, "_studio_cache_is_current", current)

    def forbidden(items):
        raise AssertionError("owner-only input must not run lossy upload preprocessing")

    monkeypatch.setattr(
        "app.pipeline.wikidata_canonical_enrichment.prepare_wikidata_upload_native_items", forbidden
    )
    items = await router._build_native_items(
        DB(), uuid.uuid4(), None, approved_only=True, source="legacy", adopt_probe=False
    )
    assert any(
        stmt.property_id == "P11603" and stmt.value == "__LOCAL:missing-person"
        for stmt in items[0].statements
    )


@pytest.mark.asyncio
async def test_owner_native_builder_refuses_unverified_cache_fallback(monkeypatch):
    import uuid

    from fastapi import HTTPException

    from app.routers import wikidata_studio as router

    class DB:
        async def execute(self, query):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    async def lookup(*args, **kwargs):
        return None

    async def no_cache(*args, **kwargs):
        return None

    monkeypatch.setattr(router, "_lookup_run_with_access", lookup)
    monkeypatch.setattr(router, "_get_studio_cache_row", no_cache)
    with pytest.raises(HTTPException) as exc:
        await router._build_native_items(
            DB(), uuid.uuid4(), None, approved_only=True, source="legacy", adopt_probe=False
        )
    assert exc.value.status_code == 409


def test_build_drops_unresolved_relations_owner_upload_preserves_absent_claims(monkeypatch):
    item = _preprocessed_item(
        [
            WikidataStatement("P11603", "__LOCAL:missing-person", "item"),
            WikidataStatement("P1574", "__LOCAL: ", "item"),
        ]
    )
    assert {stmt.property_id for stmt in item.statements} == {"P31", "P3959"}
    uploader, entity, writes = _uploader_with_real_wbi_claims(monkeypatch, _remote_statements())
    result = uploader.upload_item(item, owner_only_qid="Q123")
    assert result.status in {"updated", "exists"}
    assert all(not claim.removed for claim in entity.claims.get("P11603"))
    assert all(not claim.removed for claim in entity.claims.get("P1574"))
    assert all(
        "remove" not in claim.get_json()
        for pid in ("P11603", "P1574")
        for claim in entity.claims.get(pid)
    )
    assert len(writes) <= 1


@pytest.mark.parametrize("dry_run", [True, False])
def test_build_drops_one_relation_and_owner_blocks_partial_replacement(monkeypatch, dry_run):
    item = _preprocessed_item(
        [
            WikidataStatement("P11603", "__LOCAL:missing-person", "item"),
            WikidataStatement("P1574", "__LOCAL:missing-work", "item"),
            WikidataStatement("P1574", "Q777", "item"),
        ]
    )
    assert not any(stmt.property_id == "P11603" for stmt in item.statements)
    assert [stmt.value for stmt in item.statements if stmt.property_id == "P1574"] == ["Q777"]
    uploader, entity, writes = _uploader_with_real_wbi_claims(
        monkeypatch,
        [*_remote_statements(), WikidataStatement("P1574", "Q999", "item")],
    )

    def prepare(items, *args, **kwargs):
        it = items[0]
        return [
            wikidata_upload.PreparedItem(
                item=it,
                local_id=it.local_id,
                label="",
                entity_type="manuscript",
                existing_qid="Q123",
                method="prebuilt",
                blocked=False,
                block_status="",
                block_message="",
                ownership="own",
            )
        ]

    monkeypatch.setattr(wikidata_upload, "_make_reconciler", lambda: object())
    monkeypatch.setattr(wikidata_upload, "_prepare_for_upload", prepare)
    outcomes = wikidata_upload._owner_only_upload_sync(
        [item],
        {item.local_id: "Q123"},
        uploader,
        dry_run,
        False,
        {},
        None,
    )
    assert outcomes[0].status == "blocked"
    assert "INCOMPLETE_SOURCE" in outcomes[0].message
    assert not writes
    assert all(not claim.removed for pid in ("P11603", "P1574") for claim in entity.claims.get(pid))


def test_owner_allows_explicit_addition_without_removing_existing_claims(monkeypatch):
    item = _preprocessed_item(
        [
            WikidataStatement("P1574", "Q777", "item"),
            WikidataStatement("P1574", "Q999", "item"),
        ]
    )
    uploader, entity, writes = _uploader_with_real_wbi_claims(monkeypatch, _remote_statements())
    result = uploader.upload_item(item, owner_only_qid="Q123")
    assert result.status == "updated"
    assert len(writes) == 1
    assert all(not claim.removed for claim in entity.claims.get("P1574"))
    assert {claim.mainsnak.datavalue["value"]["id"] for claim in entity.claims.get("P1574")} == {
        "Q777",
        "Q999",
    }


def test_owner_rejects_generic_text_degraded_from_missing_source(monkeypatch):
    item = _preprocessed_item([WikidataStatement("P1574", "__LOCAL:missing-work", "item")])
    assert any(stmt.property_id == "P1574" and stmt.value == "Q234460" for stmt in item.statements)
    uploader, entity, writes = _uploader_with_real_wbi_claims(monkeypatch, _remote_statements())
    assert uploader.upload_item(item, owner_only_qid="Q123").status == "blocked"
    assert not writes
    assert all(not claim.removed for pid in ("P11603", "P1574") for claim in entity.claims.get(pid))


def test_non_owner_upload_keeps_existing_replacement_policy(monkeypatch):
    item = _preprocessed_item([])
    uploader, entity, _ = _uploader_with_real_wbi_claims(monkeypatch, _remote_statements())
    uploader._build_wbi_item(item)
    # Pass 1 keeps absent deferred links. Pass 2 removes a superseded link in the same write as its replacement.
    assert all(not claim.removed for pid in ("P11603", "P1574") for claim in entity.claims.get(pid))


@pytest.mark.parametrize("pid", ["P11603", "P1574"])
def test_owner_blocks_destructive_explicit_replacement_without_complete_source(monkeypatch, pid):
    item = _preprocessed_item([WikidataStatement(pid, "Q999", "item")])
    uploader, entity, _ = _uploader_with_real_wbi_claims(monkeypatch, _remote_statements())
    with pytest.raises(ClaimBuildError, match="OWNER_ONLY_INCOMPLETE_SOURCE"):
        uploader._build_wbi_item(item, owner_only=True)
    assert all(not claim.removed for claim in entity.claims.get(pid))
