"""Canonical Studio enrichment via legacy MARC merge (Rule W-125)."""

from __future__ import annotations

from converter.wikidata.item_models import WikidataItem, WikidataStatement
from app.pipeline.wikidata_canonical_enrichment import merge_legacy_into_canonical


def _ms(local_id: str, cn: str, *stmts: WikidataStatement) -> WikidataItem:
    return WikidataItem(
        local_id=local_id,
        entity_type="manuscript",
        records=[cn],
        labels={"he": "כותרת"},
        statements=list(stmts),
    )


def _person(local_id: str, *, viaf: str = "", label: str = "Name") -> WikidataItem:
    stmts = [
        WikidataStatement(property_id="P31", value="Q5", value_type="item"),
    ]
    if viaf:
        stmts.append(WikidataStatement(property_id="P214", value=viaf, value_type="string"))
    return WikidataItem(
        local_id=local_id,
        entity_type="person",
        labels={"en": label},
        statements=stmts,
        authority_evidence=[{"kind": "viaf", "viaf_uri": viaf, "accepted": True}] if viaf else [],
    )


def test_merge_adds_legacy_manuscript_claims_keeps_canonical_bridge() -> None:
    canonical = _ms(
        "MS_1",
        "9901",
        WikidataStatement(property_id="P31", value="Q87167", value_type="item"),
        WikidataStatement(
            property_id="P2888",
            value="https://mhm-hmo.wikibase.cloud/wiki/Item:Q9",
            value_type="url",
        ),
        WikidataStatement(property_id="P3959", value="9901", value_type="string"),
    )
    legacy = _ms(
        "legacy-ms-9901",
        "9901",
        WikidataStatement(property_id="P31", value="Q87167", value_type="item"),
        WikidataStatement(property_id="P571", value="+1600-00-00T00:00:00Z", value_type="time"),
        WikidataStatement(property_id="P407", value="Q9288", value_type="item"),
        WikidataStatement(property_id="P217", value="Heb. 1", value_type="string"),
        WikidataStatement(property_id="P186", value="Q226697", value_type="item"),
        WikidataStatement(
            property_id="P2888",
            value="https://example.invalid/wrong",
            value_type="url",
        ),
    )
    merged = merge_legacy_into_canonical([canonical], [legacy])
    assert len(merged) == 1
    item = merged[0]
    assert item.local_id == "MS_1"
    pids = {s.property_id for s in item.statements}
    assert {"P571", "P407", "P217", "P186", "P2888", "P31"} <= pids
    bridges = [s.value for s in item.statements if s.property_id == "P2888"]
    assert bridges == ["https://mhm-hmo.wikibase.cloud/wiki/Item:Q9"]


def test_merge_matches_persons_by_viaf_and_keeps_unmatched_legacy_person() -> None:
    canonical = _person("HMO_Person_1", viaf="123", label="Canonical")
    legacy_match = _person("legacy-p-123", viaf="123", label="Legacy Name")
    legacy_match.statements.append(
        WikidataStatement(property_id="P8189", value="9870123", value_type="string"),
    )
    legacy_extra = _person("legacy-p-999", viaf="999", label="Extra")
    merged = merge_legacy_into_canonical([canonical], [legacy_match, legacy_extra])
    assert len(merged) == 2
    primary = next(i for i in merged if i.local_id == "HMO_Person_1")
    assert any(s.property_id == "P8189" for s in primary.statements)
    assert any(i.local_id == "legacy-p-999" for i in merged)


def test_merge_matches_works_by_title_and_adds_author() -> None:
    canonical = WikidataItem(
        local_id="Work_1",
        entity_type="work",
        labels={"he": "משנה תורה"},
        statements=[
            WikidataStatement(property_id="P31", value="Q47461344", value_type="item"),
            WikidataStatement(property_id="P1476", value="משנה תורה", value_type="monolingualtext"),
        ],
    )
    legacy = WikidataItem(
        local_id="work:mishneh",
        entity_type="work",
        labels={"he": "משנה תורה"},
        statements=[
            WikidataStatement(property_id="P31", value="Q47461344", value_type="item"),
            WikidataStatement(property_id="P1476", value="משנה תורה", value_type="monolingualtext"),
            WikidataStatement(property_id="P50", value="Q1", value_type="item"),
        ],
        work_candidate_evidence=[{"title": "משנה תורה", "accepted": True}],
    )
    merged = merge_legacy_into_canonical([canonical], [legacy])
    assert len(merged) == 1
    assert any(s.property_id == "P50" and s.value == "Q1" for s in merged[0].statements)
    assert merged[0].work_candidate_evidence


def test_merge_drops_identifierless_person_items() -> None:
    """Canonical enrichment must never reintroduce a non-notable person."""
    invalid = _person("mazal:987007257211705171", label="Aaron")
    valid = _person("mazal:987007257436505171", label="Aaron ben David")
    valid.statements.append(
        WikidataStatement(
            property_id="P8189",
            value="987007257436505171",
            value_type="string",
        ),
    )

    assert merge_legacy_into_canonical([], [invalid]) == []
    assert [item.local_id for item in merge_legacy_into_canonical([], [valid])] == [
        "mazal:987007257436505171",
    ]
    assert [item.local_id for item in merge_legacy_into_canonical([invalid], [valid])] == [
        "mazal:987007257436505171",
    ]

    assert merge_legacy_into_canonical([invalid], []) == []
