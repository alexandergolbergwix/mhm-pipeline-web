import copy
from types import SimpleNamespace

import pytest
from wikibaseintegrator import WikibaseIntegrator
from wikibaseintegrator.wbi_enums import ActionIfExists

from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.uploader import WikidataUploader


def _item(statements, *, include_p31=False, labels=None, descriptions=None):
    return WikidataItem(
        local_id="QDraft_MS_10001",
        entity_type="manuscript",
        existing_qid="Q123",
        records=["10001"],
        labels=labels if labels is not None else {"en": "Manuscript"},
        descriptions=descriptions or {},
        statements=[
            WikidataStatement("P3959", "10001", "external-id"),
            *([WikidataStatement("P31", "Q87167", "item")] if include_p31 else []),
            *statements,
        ],
    )


def _setup(
    monkeypatch, extra, *, label="Manuscript", description="Existing description", include_p31=False
):
    uploader = WikidataUploader(token="fixture", allow_live=True)
    entity = WikibaseIntegrator().item.new()
    entity.id = "Q123"
    entity.lastrevid = 42
    entity.labels.set("en", label)
    entity.descriptions.set("en", description)
    statements = [
        WikidataStatement("P3959", "10001", "external-id"),
        *([WikidataStatement("P31", "Q87167", "item")] if include_p31 else []),
        *extra,
    ]
    for index, statement in enumerate(statements):
        claim = uploader._build_claim(statement)
        assert claim is not None
        claim.id = f"Q123$00000000-0000-0000-0000-{index + 1:012d}"
        entity.claims.add(claim, action_if_exists=ActionIfExists.FORCE_APPEND)
    payloads = []

    def write(**kwargs):
        payloads.append((copy.deepcopy(entity.get_json()), kwargs))
        return entity

    entity.write = write
    monkeypatch.setattr(
        uploader,
        "_init_wbi",
        lambda: SimpleNamespace(item=SimpleNamespace(get=lambda *args, **kwargs: entity)),
    )
    monkeypatch.setattr(uploader, "owner_only_target_valid", lambda qid, cn: True)
    monkeypatch.setattr(uploader, "_owner_only_revision", lambda qid, cn: 42)
    monkeypatch.setattr(uploader, "_is_our_item", lambda *args, **kwargs: True)
    monkeypatch.setattr(uploader, "_assert_modifiable", lambda *args, **kwargs: None)
    monkeypatch.setattr(uploader, "_bot_excluded", lambda qid: False)
    monkeypatch.setattr(uploader, "_rate_limit", lambda: None)
    uploader._is_our_item_cache["Q123"] = True
    return uploader, entity, payloads


def _reference(url):
    return {"property": "P854", "value": url, "type": "url"}


def test_no_p31_adds_existing_pid_value_and_preserves_existing_metadata(monkeypatch):
    old_relation = WikidataStatement(
        "P11603",
        "Q888",
        "item",
        rank="preferred",
        references=[_reference("https://example.org/old")],
    )
    old_url = WikidataStatement("P973", "https://example.org/old-page", "url")
    uploader, entity, payloads = _setup(monkeypatch, [old_relation, old_url])
    old_claim = copy.deepcopy(entity.claims.get("P11603")[0].get_json())
    proposed = _item(
        [
            WikidataStatement("P11603", "Q888", "item", rank="preferred"),
            WikidataStatement("P973", "https://example.org/new-page", "url"),
        ]
    )
    result = uploader.upload_item(proposed, owner_only_qid="Q123")
    assert result.status == "updated" and len(payloads) == 1
    payload, kwargs = payloads[0]
    assert kwargs["baserevid"] == 42
    assert payload["descriptions"]["en"]["value"] == "Existing description"
    assert payload["claims"]["P11603"][0] == old_claim
    assert {claim["mainsnak"]["datavalue"]["value"] for claim in payload["claims"]["P973"]} == {
        "https://example.org/old-page",
        "https://example.org/new-page",
    }


def test_no_p31_adds_relation_without_changing_old_relation(monkeypatch):
    old = WikidataStatement(
        "P11603",
        "Q888",
        "item",
        rank="preferred",
        references=[_reference("https://example.org/old")],
    )
    uploader, entity, payloads = _setup(monkeypatch, [old])
    before = copy.deepcopy(entity.claims.get("P11603")[0].get_json())
    result = uploader.upload_item(
        _item([WikidataStatement("P11603", "Q999", "item")]), owner_only_qid="Q123"
    )
    assert result.status == "updated" and len(payloads) == 1
    claims = payloads[0][0]["claims"]["P11603"]
    assert claims[0] == before
    assert {claim["mainsnak"]["datavalue"]["value"]["id"] for claim in claims} == {"Q888", "Q999"}


@pytest.mark.parametrize("include_p31", [False, True])
def test_equal_claim_rank_conflict_blocks_without_losing_references(monkeypatch, include_p31):
    old = WikidataStatement(
        "P11603",
        "Q888",
        "item",
        rank="preferred",
        references=[_reference("https://example.org/old")],
    )
    uploader, entity, payloads = _setup(monkeypatch, [old], include_p31=include_p31)
    before = copy.deepcopy(entity.get_json())
    proposed = _item(
        [WikidataStatement("P11603", "Q888", "item", rank="normal")], include_p31=include_p31
    )
    result = uploader.upload_item(proposed, owner_only_qid="Q123")
    assert result.status == "blocked" and "rank" in result.message
    assert not payloads and entity.get_json() == before


def test_equal_claim_adds_new_reference_without_replacing_old_reference(monkeypatch):
    old = WikidataStatement(
        "P11603",
        "Q888",
        "item",
        rank="preferred",
        references=[_reference("https://example.org/old")],
    )
    uploader, entity, payloads = _setup(monkeypatch, [old])
    proposed = _item(
        [
            WikidataStatement(
                "P11603",
                "Q888",
                "item",
                rank="preferred",
                references=[_reference("https://example.org/new")],
            )
        ]
    )
    result = uploader.upload_item(proposed, owner_only_qid="Q123")
    assert result.status == "updated" and len(payloads) == 1
    claim = payloads[0][0]["claims"]["P11603"][0]
    assert claim["rank"] == "preferred"
    assert {ref["snaks"]["P854"][0]["datavalue"]["value"] for ref in claim["references"]} == {
        "https://example.org/old",
        "https://example.org/new",
    }
    assert entity.claims.get("P11603")[0].id == "Q123$00000000-0000-0000-0000-000000000002"


@pytest.mark.parametrize("include_p31", [False, True])
def test_matching_claim_keeps_all_existing_reference_objects(monkeypatch, include_p31):
    old = WikidataStatement(
        "P11603",
        "Q888",
        "item",
        rank="preferred",
        references=[_reference("https://example.org/old")],
    )
    uploader, entity, payloads = _setup(monkeypatch, [old], include_p31=include_p31)
    claim = entity.claims.get("P11603")[0]
    claim.references.add(copy.deepcopy(list(claim.references)[0]))
    before = copy.deepcopy(claim.get_json())
    proposed = _item(
        [
            WikidataStatement("P11603", "Q888", "item", rank="preferred"),
            WikidataStatement("P953", "https://example.org/digitized", "url"),
        ],
        include_p31=include_p31,
    )
    result = uploader.upload_item(proposed, owner_only_qid="Q123")
    assert result.status == "updated" and len(payloads) == 1
    assert payloads[0][0]["claims"]["P11603"][0] == before
    assert len(payloads[0][0]["claims"]["P11603"][0]["references"]) == 2


def test_equal_value_with_different_qualifiers_blocks(monkeypatch):
    uploader, entity, payloads = _setup(monkeypatch, [WikidataStatement("P11603", "Q888", "item")])
    before = copy.deepcopy(entity.get_json())
    proposed = _item(
        [
            WikidataStatement(
                "P11603",
                "Q888",
                "item",
                qualifiers=[{"property": "P1932", "value": "Name", "type": "string"}],
            )
        ]
    )
    result = uploader.upload_item(proposed, owner_only_qid="Q123")
    assert result.status == "blocked" and "qualifiers" in result.message
    assert not payloads and entity.get_json() == before


def test_somevalue_and_novalue_do_not_match_each_other(monkeypatch):
    old = WikidataStatement("P11603", None, "novalue")
    uploader, entity, payloads = _setup(monkeypatch, [old])
    before = copy.deepcopy(entity.get_json())
    result = uploader.upload_item(
        _item([WikidataStatement("P11603", None, "somevalue")]), owner_only_qid="Q123"
    )
    assert result.status == "blocked" and "snak" in result.message
    assert not payloads and entity.get_json() == before


@pytest.mark.parametrize(
    "labels,descriptions",
    [
        ({"en": "Manuscript"}, {"en": "Manuscript"}),
        ({"en": "Existing description"}, {}),
        ({"en": "Manuscript"}, {"en": "Different description"}),
        ({"en": "Manuscript"}, {"en": ""}),
    ],
)
def test_description_or_label_conflicts_block_without_removing_live_description(
    monkeypatch,
    labels,
    descriptions,
):
    uploader, entity, payloads = _setup(monkeypatch, [])
    before = copy.deepcopy(entity.get_json())
    result = uploader.upload_item(
        _item([], labels=labels, descriptions=descriptions), owner_only_qid="Q123"
    )
    assert result.status == "blocked" and "METADATA" in result.message
    assert not payloads and entity.get_json() == before
    assert entity.descriptions.get("en").value == "Existing description"
    assert not entity.descriptions.get("en").removed


def test_duplicate_equal_live_claims_fail_closed(monkeypatch):
    old = WikidataStatement(
        "P11603", "Q888", "item", references=[_reference("https://example.org/one")]
    )
    other = WikidataStatement(
        "P11603", "Q888", "item", references=[_reference("https://example.org/two")]
    )
    uploader, entity, payloads = _setup(monkeypatch, [old, other])
    before = copy.deepcopy(entity.get_json())
    result = uploader.upload_item(
        _item([WikidataStatement("P11603", "Q888", "item")]), owner_only_qid="Q123"
    )
    assert result.status == "blocked" and "ambiguous" in result.message
    assert not payloads and entity.get_json() == before


def test_empty_source_alias_does_not_remove_existing_aliases(monkeypatch):
    uploader, entity, payloads = _setup(monkeypatch, [])
    entity.aliases.set("en", "Old alias")
    before = copy.deepcopy(entity.get_json())
    source = _item([])
    source.aliases = {"en": [""]}
    result = uploader.upload_item(source, owner_only_qid="Q123")
    assert result.status == "blocked" and "alias" in result.message
    assert not payloads and entity.get_json() == before


def test_alias_only_addition_is_written_without_removing_old_alias(monkeypatch):
    uploader, entity, payloads = _setup(monkeypatch, [])
    entity.aliases.set("en", "Old alias")
    source = _item([])
    source.aliases = {"en": ["New alias"]}
    result = uploader.upload_item(source, owner_only_qid="Q123")
    assert result.status == "updated" and len(payloads) == 1
    aliases = payloads[0][0]["aliases"]["en"]
    assert {alias["value"] for alias in aliases} == {"Old alias", "New alias"}
    assert all("remove" not in alias for alias in aliases)


@pytest.mark.parametrize(
    "reference",
    [
        {"property": "P854", "value": "", "type": "url"},
        {"property": "invalid", "value": "https://example.org/source", "type": "url"},
        {"property": "P854", "value": "text", "type": "unknown-type"},
        {"snaks": [{"property": "P854", "value": "https://example.org/source"}]},
    ],
)
def test_malformed_source_reference_blocks_instead_of_disappearing(monkeypatch, reference):
    uploader, entity, payloads = _setup(monkeypatch, [WikidataStatement("P11603", "Q888", "item")])
    before = copy.deepcopy(entity.get_json())
    source = _item([WikidataStatement("P11603", "Q888", "item", references=[reference])])
    result = uploader.upload_item(source, owner_only_qid="Q123")
    assert result.status == "blocked" and "reference" in result.message
    assert not payloads and entity.get_json() == before


def test_owner_preserves_external_id_reference_datatype(monkeypatch):
    uploader, _, payloads = _setup(monkeypatch, [])
    source = _item(
        [
            WikidataStatement(
                "P953",
                "https://example.org/item",
                "url",
                references=[{"property": "P3959", "value": "10001", "type": "external-id"}],
            )
        ]
    )
    result = uploader.upload_item(source, owner_only_qid="Q123")
    assert result.status == "updated" and len(payloads) == 1
    snak = payloads[0][0]["claims"]["P953"][0]["references"][0]["snaks"]["P3959"][0]
    assert snak["datatype"] == "external-id" and snak["datavalue"]["value"] == "10001"


@pytest.mark.parametrize("rank", [None, "", "NORMAL", 0, []])
def test_invalid_source_rank_blocks_before_write(monkeypatch, rank):
    uploader, entity, payloads = _setup(monkeypatch, [WikidataStatement("P11603", "Q888", "item")])
    before = copy.deepcopy(entity.get_json())
    source = WikidataStatement("P11603", "Q888", "item")
    source.rank = rank
    result = uploader.upload_item(_item([source]), owner_only_qid="Q123")
    assert result.status == "blocked" and "rank" in result.message
    assert not payloads and entity.get_json() == before


@pytest.mark.parametrize("conflict", [False, True])
def test_preview_uses_the_same_no_p31_metadata_guard(monkeypatch, conflict):
    from app.pipeline import wikidata_upload

    old = WikidataStatement(
        "P11603",
        "Q888",
        "item",
        rank="preferred",
        references=[_reference("https://example.org/old")],
    )
    uploader, _, payloads = _setup(monkeypatch, [old])
    proposed = _item(
        [
            WikidataStatement("P11603", "Q888", "item", rank="normal" if conflict else "preferred"),
            WikidataStatement("P953", "https://example.org/digitized", "url"),
        ]
    )

    def prepare(items, *args, **kwargs):
        candidate = items[0]
        return [
            wikidata_upload.PreparedItem(
                item=candidate,
                local_id=candidate.local_id,
                label="Manuscript",
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
        [proposed],
        {proposed.local_id: "Q123"},
        uploader,
        True,
        False,
        {},
        None,
    )
    assert outcomes[0].status == ("blocked" if conflict else "would_update")
    assert not payloads


def test_owner_claim_builder_refuses_creation(monkeypatch):
    from converter.wikidata.uploader import ClaimBuildError

    uploader, _, payloads = _setup(monkeypatch, [])
    source = _item([])
    source.existing_qid = None
    with pytest.raises(ClaimBuildError, match="existing QID"):
        uploader._build_wbi_item(source, owner_only=True)
    assert not payloads


@pytest.mark.parametrize(
    "unit",
    [
        "furlong",
        "MM",
        " mm",
        "mm ",
        "q174789",
        "Q0",
        "Q0174789",
        "https://www.wikidata.org/entity/Q174789",
        "http://evil.example/entity/Q174789",
        "http://www.wikidata.org/entity/Q174789/extra",
        0,
        False,
        [],
    ],
)
@pytest.mark.parametrize("dry_run", [True, False])
def test_owner_rejects_unknown_and_noncanonical_quantity_units(monkeypatch, unit, dry_run):
    from app.pipeline import wikidata_upload

    uploader, _, payloads = _setup(monkeypatch, [])
    source = _item([WikidataStatement("P1104", "12", "quantity", unit=unit)])

    def prepare(items, *args, **kwargs):
        candidate = items[0]
        return [
            wikidata_upload.PreparedItem(
                item=candidate,
                local_id=candidate.local_id,
                label="Manuscript",
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
        [source],
        {source.local_id: "Q123"},
        uploader,
        dry_run,
        False,
        {},
        None,
    )
    assert outcomes[0].status == "blocked" and "UNIT" in outcomes[0].message
    assert not payloads


@pytest.mark.parametrize(
    "unit,expected",
    [
        ("", "1"),
        (None, "1"),
        ("1", "1"),
        ("mm", "http://www.wikidata.org/entity/Q174789"),
        ("leaf", "http://www.wikidata.org/entity/Q107256474"),
        ("Q174789", "http://www.wikidata.org/entity/Q174789"),
        ("http://www.wikidata.org/entity/Q174789", "http://www.wikidata.org/entity/Q174789"),
    ],
)
def test_owner_writes_only_canonical_quantity_units(monkeypatch, unit, expected):
    uploader, _, payloads = _setup(monkeypatch, [])
    source = _item([WikidataStatement("P1104", "12", "quantity", unit=unit)])
    result = uploader.upload_item(source, owner_only_qid="Q123")
    assert result.status == "updated" and len(payloads) == 1
    assert (
        payloads[0][0]["claims"]["P1104"][0]["mainsnak"]["datavalue"]["value"]["unit"] == expected
    )


@pytest.mark.parametrize("unit", [0, False, [], {}])
def test_owner_native_conversion_preserves_invalid_quantity_unit(unit):
    from app.routers.wikidata_studio import studio_dict_to_native_item
    from converter.wikidata.uploader import ClaimBuildError

    raw = {
        "local_id": "QDraft_MS_10001",
        "entity_type": "manuscript",
        "records": ["10001"],
        "statements": [
            {"property_id": "P1104", "value": "12", "value_type": "quantity", "unit": unit}
        ],
    }
    native = studio_dict_to_native_item(raw, strict_quantity_unit=True)
    uploader = WikidataUploader(token="fixture", allow_live=True)
    with pytest.raises(ClaimBuildError, match="OWNER_ONLY_UNIT"):
        uploader._build_claim(native.statements[0], owner_only=True)


def test_general_quantity_conversion_rejects_unknown_unit():
    from converter.wikidata.uploader import ClaimBuildError

    uploader = WikidataUploader(token="fixture", allow_live=True)
    with pytest.raises(ClaimBuildError, match="quantity unit"):
        uploader._build_claim(WikidataStatement("P1104", "12", "quantity", unit="furlong"))


def _qualified_relation(values):
    return WikidataStatement(
        "P11603",
        "Q888",
        "item",
        qualifiers=[{"property": "P1932", "value": value, "type": "item"} for value in values],
    )


@pytest.mark.parametrize(
    "old_values,new_values",
    [
        (["Q100", "Q100"], ["Q100", "Q200"]),
        (["Q100", "Q200"], ["Q100", "Q100"]),
    ],
)
@pytest.mark.parametrize("include_p31", [True, False])
@pytest.mark.parametrize("dry_run", [True, False])
def test_owner_blocks_distinct_qualifier_multisets_even_if_wbi_equal(
    monkeypatch,
    old_values,
    new_values,
    include_p31,
    dry_run,
):
    from app.pipeline import wikidata_upload

    old = _qualified_relation(old_values)
    uploader, entity, payloads = _setup(monkeypatch, [old], include_p31=include_p31)
    new = _qualified_relation(new_values)
    old_claim = entity.claims.get("P11603")[0]
    new_claim = uploader._build_claim(new, owner_only=True)
    assert old_claim == new_claim or new_claim == old_claim
    before = copy.deepcopy(entity.get_json())
    source = _item([new], include_p31=include_p31)

    def prepare(items, *args, **kwargs):
        candidate = items[0]
        return [
            wikidata_upload.PreparedItem(
                item=candidate,
                local_id=candidate.local_id,
                label="Manuscript",
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
        [source],
        {source.local_id: "Q123"},
        uploader,
        dry_run,
        False,
        {},
        None,
    )
    assert outcomes[0].status == "blocked"
    assert not payloads and entity.get_json() == before


@pytest.mark.parametrize("include_p31", [True, False])
def test_owner_accepts_equal_qualifier_multisets_in_any_order(monkeypatch, include_p31):
    uploader, entity, payloads = _setup(
        monkeypatch, [_qualified_relation(["Q100", "Q200", "Q100"])], include_p31=include_p31
    )
    before = copy.deepcopy(entity.get_json())
    source = _item([_qualified_relation(["Q200", "Q100", "Q100"])], include_p31=include_p31)
    result = uploader.upload_item(source, owner_only_qid="Q123")
    assert result.status == "exists"
    assert not payloads and entity.get_json() == before


def test_owner_ignores_qualifier_hash_when_semantics_match(monkeypatch):
    uploader, entity, payloads = _setup(monkeypatch, [_qualified_relation(["Q100", "Q200"])])
    old = entity.claims.get("P11603")[0]
    old.qualifiers.get("P1932")[0].hash = "fixture-hash"
    before = copy.deepcopy(entity.get_json())
    result = uploader.upload_item(
        _item([_qualified_relation(["Q200", "Q100"])]), owner_only_qid="Q123"
    )
    assert result.status == "exists"
    assert not payloads and entity.get_json() == before


@pytest.mark.parametrize("include_p31", [True, False])
def test_owner_blocks_malformed_existing_qualifier(monkeypatch, include_p31):
    uploader, entity, payloads = _setup(
        monkeypatch, [_qualified_relation(["Q100"])], include_p31=include_p31
    )
    entity.claims.get("P11603")[0].qualifiers.get("P1932")[0].datatype = None
    source = _item([_qualified_relation(["Q100"])], include_p31=include_p31)
    result = uploader.upload_item(source, owner_only_qid="Q123")
    assert result.status == "blocked" and "METADATA" in result.message
    assert not payloads


def test_owner_checks_quantity_unit_before_preparation_can_drop_it(monkeypatch):
    from app.pipeline import wikidata_upload

    uploader, _, payloads = _setup(monkeypatch, [])
    source = _item([WikidataStatement("P2048", "5000", "quantity", unit="unknown")])

    def forbidden(*args, **kwargs):
        raise AssertionError("preparation must not hide the invalid unit")

    monkeypatch.setattr(wikidata_upload, "_prepare_for_upload", forbidden)
    result = wikidata_upload._owner_only_upload_sync(
        [source],
        {source.local_id: "Q123"},
        uploader,
        True,
        False,
        {},
        None,
    )
    assert result[0].status == "blocked" and "UNIT" in result[0].message
    assert not payloads
