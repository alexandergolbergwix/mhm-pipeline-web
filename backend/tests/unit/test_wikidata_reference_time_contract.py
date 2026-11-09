"""Reference time validation at cache, export, and upload boundaries."""

from __future__ import annotations

import copy

import pytest

from app.pipeline.wikidata_studio import studio_cache_has_stale_validation, validate_upload_reference_blocks
from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.quickstatements import QuickStatementsExporter, validate_source_references
from converter.wikidata.uploader import ClaimBuildError, WikidataUploader


def _item(snak: dict[str, object], *, blocks: bool) -> WikidataItem:
    references = [copy.deepcopy(snak)]
    return WikidataItem(existing_qid="Q123", statements=[WikidataStatement(
        "P217", "MS 1", "string", references=references,
        reference_blocks=[copy.deepcopy(references)] if blocks else [],
    )])


def _cache_item(item: WikidataItem) -> dict[str, object]:
    statement = item.statements[0]
    return {"statements": [{"references": statement.references, "reference_blocks": statement.reference_blocks}]}


def _uploader() -> WikidataUploader:
    return WikidataUploader(token="Fixture@Bot:not-a-credential", is_test=False, allow_live=True)


def _assert_qs_reference_omitted(item: WikidataItem) -> None:
    output = QuickStatementsExporter().export_item(item)
    assert "Item omitted: Q123" in output
    assert "invalid or unsupported reference" in output
    assert "\tP217\t" not in output


@pytest.mark.parametrize("blocks", [False, True])
@pytest.mark.parametrize("precision", [0, 12, 13, 14, 15, -1, True, "11", None])
def test_invalid_reference_precision_is_rejected_at_each_boundary(precision: object, blocks: bool) -> None:
    snak = {"property": "P813", "value": "+2020-01-02T00:00:00Z", "type": "time", "precision": precision}
    item = _item(snak, blocks=blocks)
    stmt = item.statements[0]
    assert studio_cache_has_stale_validation([_cache_item(item)])
    with pytest.raises(ValueError):
        validate_source_references(stmt.references, stmt.reference_blocks, require_proven_blocks=True)
    with pytest.raises(ValueError):
        validate_upload_reference_blocks([item])
    _assert_qs_reference_omitted(item)
    with pytest.raises(ClaimBuildError):
        _uploader()._build_claim(stmt)


@pytest.mark.parametrize("precision", [0, 12])
@pytest.mark.parametrize("owner_only", [False, True])
def test_invalid_reference_precision_blocks_before_write(
    monkeypatch: pytest.MonkeyPatch, precision: int, owner_only: bool,
) -> None:
    snak = {"property": "P813", "value": "+2020-01-02T00:00:00Z", "type": "time", "precision": precision}
    item = _item(snak, blocks=True)
    item.local_id = "QDraft_MS_10001"
    item.entity_type = "manuscript"
    item.records = ["10001"]
    item.statements.insert(0, WikidataStatement("P3959", "10001", "external-id"))
    uploader = _uploader()
    initialized: list[bool] = []
    monkeypatch.setattr(uploader, "_check_moratorium_for_live", lambda: None)
    monkeypatch.setattr(uploader, "_init_wbi", lambda: initialized.append(True))
    result = uploader.upload_item(item, owner_only_qid="Q123" if owner_only else None)
    assert result.status == "blocked"
    assert "reference time" in result.message
    assert initialized == []


@pytest.mark.parametrize("owner_only", [False, True])
@pytest.mark.parametrize("precision,value", [
    (1, "+1500-00-00T00:00:00Z"),
    (9, "+1500-00-00T00:00:00Z"),
    (10, "+2020-02-00T00:00:00Z"),
    (11, "+2020-02-29T00:00:00Z"),
])
def test_valid_reference_time_is_fresh_and_written_with_same_precision(
    precision: int, value: str, owner_only: bool,
) -> None:
    snak = {"property": "P813", "value": value, "type": "time", "precision": precision}
    item = _item(snak, blocks=True)
    assert not studio_cache_has_stale_validation([_cache_item(item)])
    validate_upload_reference_blocks([item])
    assert f"S813\t{value}/{precision}" in QuickStatementsExporter().export_item(item)
    claim = _uploader()._build_claim(item.statements[0], owner_only=owner_only)
    written = claim.get_json()["references"][0]["snaks"]["P813"][0]["datavalue"]["value"]
    assert written["precision"] == precision
    assert written["time"] == value


@pytest.mark.parametrize("snak", [
    {"property": "P813", "value": "+2020-01-02T01:00:00Z", "type": "time", "precision": 11},
    {"property": "P813", "value": "+2020-00-00T00:00:00Z", "type": "time", "precision": 11},
    {"property": "P813", "value": "+2020-02-30T00:00:00Z", "type": "time", "precision": 11},
    {"property": "P813", "value": "+2020-01-02T00:00:00Z", "type": "time", "precision": 11, "extra": "lost"},
    {"property": "P813", "value": "+2020-01-02T00:00:00Z", "type": "time", "precision": 11, "before": -1},
    {"property": "P813", "value": "+2020-01-02T00:00:00Z", "type": ""},
    {"property": "P813", "value": "+2020-01-02T00:00:00Z", "type": "time", "value_type": []},
])
def test_invalid_reference_time_or_metadata_is_rejected_at_each_boundary(snak: dict[str, object]) -> None:
    item = _item(snak, blocks=True)
    stmt = item.statements[0]
    assert studio_cache_has_stale_validation([_cache_item(item)])
    with pytest.raises(ValueError):
        validate_upload_reference_blocks([item])
    _assert_qs_reference_omitted(item)
    with pytest.raises(ClaimBuildError):
        _uploader()._build_claim(stmt)


def test_flat_view_metadata_cannot_bypass_the_cache_gate() -> None:
    snak = {"property": "P813", "value": "+2020-01-02T00:00:00Z", "type": "time", "precision": 11}
    item = _item(snak, blocks=True)
    stmt = item.statements[0]
    stmt.references[0]["hash"] = "a" * 40
    assert studio_cache_has_stale_validation([_cache_item(item)])
    with pytest.raises(ValueError, match="unsupported metadata"):
        validate_upload_reference_blocks([item])
    _assert_qs_reference_omitted(item)
    with pytest.raises(ClaimBuildError, match="unsupported reference metadata"):
        _uploader()._build_claim(stmt)


@pytest.mark.parametrize("owner_only", [False, True])
def test_explicit_julian_calendar_is_not_changed(owner_only: bool) -> None:
    calendar = "http://www.wikidata.org/entity/Q1985786"
    snak = {"property": "P813", "value": "+1500-02-29T00:00:00Z", "type": "time", "precision": 11,
            "calendarmodel": calendar, "before": 2, "after": 3, "timezone": 60}
    item = _item(snak, blocks=True)
    assert studio_cache_has_stale_validation([_cache_item(item)])
    _assert_qs_reference_omitted(item)
    uploader = _uploader()
    if owner_only:
        uploader._owner_only_session = object()
    claim = uploader._build_claim(item.statements[0], owner_only=owner_only)
    written = claim.get_json()["references"][0]["snaks"]["P813"][0]["datavalue"]["value"]
    assert written["time"] == snak["value"]
    assert written["calendarmodel"] == calendar
    assert (written["before"], written["after"], written["timezone"]) == (2, 3, 60)


def test_gregorian_and_julian_leap_years_are_not_interchanged() -> None:
    snak = {"property": "P813", "value": "+1500-02-29T00:00:00Z", "type": "time", "precision": 11,
            "calendarmodel": "http://www.wikidata.org/entity/Q1985727"}
    with pytest.raises(ClaimBuildError, match="invalid reference time"):
        _uploader()._build_claim(_item(snak, blocks=True).statements[0])
