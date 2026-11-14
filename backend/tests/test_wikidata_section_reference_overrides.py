"""Section CSV must preserve overrides already present in a cached build."""

import csv
import json
from types import SimpleNamespace

import pytest

from app.models.item_override import WikidataItemOverride
from app.models.wikidata_studio_cache import WikidataStudioCache
from app.pipeline.wikidata_studio_batches import compute_build_fingerprint_streamed


@pytest.mark.asyncio
async def test_section_csv_cache_hit_preserves_built_reference_overrides(
    sample_run, db_session, monkeypatch,
):
    from app.pipeline import wikidata_studio
    from app.pipeline.wikidata_item_merge import apply_wikidata_item_override, override_row_to_dict

    run_id = sample_run["run_id"]
    old = {"property": "P854", "value": "https://example.org/old", "type": "url"}
    new = {"property": "P854", "value": "https://example.org/new", "type": "url"}
    item = {
        "local_id": "work:1", "entity_type": "work", "labels": {"en": "Old"},
        "statements": [
            {"property_id": "P31", "value": "Q5", "value_type": "item",
             "references": [old], "reference_blocks": [[old]]},
            {"property_id": "P2093", "value": "Remove", "value_type": "string"},
        ],
        "claims": [{"property_id": "P31", "value": "Q999", "value_type": "item"}],
    }
    override = WikidataItemOverride(
        run_id=run_id, local_id="work:1", labels={"en": "Edited"},
        statement_edits={"0": {"references": [new], "reference_blocks": [[new]]}},
        remove_statements=[1],
        add_statements=[{"property_id": "P1476", "value": "Title", "value_type": "string"}],
        approved=True,
    )
    db_session.add(override)
    await db_session.flush()
    built_item = apply_wikidata_item_override(item, override_row_to_dict(override))
    fingerprint = await compute_build_fingerprint_streamed(db_session, run_id, approved_only=True)
    db_session.add(WikidataStudioCache(
        run_id=run_id, approved_only=True, source="legacy", input_fingerprint=fingerprint,
        result_items=[built_item], quickstatements="", summary={}, record_count=1,
    ))
    await db_session.commit()

    async def fail_build(*args, **kwargs):
        raise AssertionError("The section export must use the cached item")

    monkeypatch.setattr(wikidata_studio, "build_items_for_run", fail_build)
    client = sample_run["client"]
    export = await client.get(f"/api/runs/{run_id}/wikidata-studio/export?format=csv")
    assert export.status_code == 200
    row = next(csv.DictReader(export.text.splitlines()))
    assert row["label_en"] == "Edited"
    assert json.loads(row["statements_json"]) == built_item["statements"]
    assert item["statements"][0]["reference_blocks"] == [[old]]


@pytest.mark.asyncio
async def test_section_csv_marks_inconsistent_reference_override(
    sample_run, db_session, monkeypatch,
):
    from app.pipeline import wikidata_studio

    run_id = sample_run["run_id"]
    old = {"property": "P854", "value": "https://example.org/old", "type": "url"}
    new = {"property": "P854", "value": "https://example.org/new", "type": "url"}
    item = {
        "local_id": "work:1", "entity_type": "work", "labels": {"en": "Work"},
        "statements": [{"property_id": "P31", "value": "Q5", "value_type": "item",
                        "references": [old], "reference_blocks": [[old]]}],
    }
    item["statements"][0]["reference_blocks"] = [[new]]
    db_session.add(WikidataItemOverride(
        run_id=run_id, local_id="work:1", statement_edits={"0": {"reference_blocks": [[new]]}},
    ))
    await db_session.flush()
    fingerprint = await compute_build_fingerprint_streamed(db_session, run_id, approved_only=True)
    db_session.add(WikidataStudioCache(
        run_id=run_id, approved_only=True, source="legacy", input_fingerprint=fingerprint,
        result_items=[item], quickstatements="", summary={}, record_count=1,
    ))
    await db_session.commit()

    async def fail_build(*args, **kwargs):
        raise AssertionError("The section export must use the cached item")

    monkeypatch.setattr(wikidata_studio, "build_items_for_run", fail_build)
    response = await sample_run["client"].get(
        f"/api/runs/{run_id}/wikidata-studio/export?format=csv",
    )
    assert response.status_code == 200
    row = next(csv.DictReader(response.text.splitlines()))
    assert any(issue["code"] == "REFERENCE_PROVENANCE_UNVERIFIED"
               for issue in json.loads(row["validation_issues_json"]))
    assert json.loads(row["statements_json"])[0]["reference_blocks"] == [[new]]


_OLD_REF = {"property": "P854", "value": "https://example.org/old", "type": "url"}
_NEW_REF = {"property": "P854", "value": "https://example.org/new", "type": "url"}


def _reference_only_patch_item(reference_blocks):
    item = {
        "local_id": "work:1", "entity_type": "work", "labels": {"en": "Work"},
        "statements": [{"property_id": "P31", "value": "Q5", "value_type": "item",
                        "references": [_OLD_REF, _NEW_REF],
                        "reference_blocks": [[_OLD_REF], [_NEW_REF]]}],
    }
    override = SimpleNamespace(add_statements=[], statement_edits={
        "0": {"reference_blocks": reference_blocks},
    })
    return item, override


def test_valid_reference_blocks_only_patch_is_not_flagged():
    from app.routers.wikidata_studio import (
        _reference_source_with_override,
        _with_reference_provenance_finding,
    )

    item, override = _reference_only_patch_item([[_OLD_REF, _NEW_REF]])
    source = _reference_source_with_override(item, override)
    assert _with_reference_provenance_finding(item, reference_source=source) is item


def test_inconsistent_reference_blocks_only_patch_is_flagged():
    from app.routers.wikidata_studio import (
        _reference_source_with_override,
        _with_reference_provenance_finding,
    )

    item, override = _reference_only_patch_item([[_NEW_REF]])
    source = _reference_source_with_override(item, override)
    finding = _with_reference_provenance_finding(item, reference_source=source)
    assert any(issue["code"] == "REFERENCE_PROVENANCE_UNVERIFIED"
               for issue in finding["validation_issues"])


@pytest.mark.asyncio
@pytest.mark.parametrize("approval", [
    {"accept_foreign_modify": True},
    {"accepted_foreign_qid": "Q123"},
    {"accept_foreign_modify": True, "accepted_foreign_qid": "Q123"},
])
async def test_item_override_cannot_grant_permission_for_foreign_edits(sample_run, approval):
    response = await sample_run["client"].patch(
        f"/api/runs/{sample_run['run_id']}/wikidata-studio/items/manuscript:1",
        json=approval,
    )
    assert response.status_code == 422
    assert "Foreign edits are disabled" in response.json()["detail"]
