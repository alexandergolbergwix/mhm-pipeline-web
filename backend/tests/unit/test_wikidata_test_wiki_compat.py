"""test.wikidata.org smoke-path claim remap + filter (Rules W-182 / W-183)."""

from __future__ import annotations

from types import SimpleNamespace

from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.test_wiki_compat import (
    WikiTestAdaptResult,
    WikiTestAdaptStats,
    choose_test_item,
    choose_test_property,
    collect_test_wiki_ids,
    description_is_mhm_stub_for,
    expected_wikibase_datatype,
    filter_item_for_test_wiki,
    format_test_wiki_outcome_note,
    item_target_qid,
    mhm_test_stub_description,
    parse_wbeditentity_conflict_id,
    pid_map_key,
    pid_map_lookup,
    pid_map_store,
    rewrite_item_with_maps,
)
from converter.wikidata.uploader import WikidataUploader


def test_expected_datatypes() -> None:
    assert expected_wikibase_datatype("item") == "wikibase-item"
    assert expected_wikibase_datatype("monolingualtext") == "monolingualtext"
    assert expected_wikibase_datatype("bogus") is None


def test_item_target_qid() -> None:
    assert item_target_qid("Q87167") == "Q87167"
    assert item_target_qid("q12") == "Q12"
    assert item_target_qid("not-a-qid") is None


def test_choose_test_property_rejects_fuzzy_label() -> None:
    chosen = choose_test_property(
        "P31",
        "item",
        property_label="instance of",
        property_datatypes={"P31": "url"},
        pid_map={},
        search_hits=[
            {"id": "P85140", "label": "instance of:jsiavb", "datatype": "wikibase-item"},
        ],
    )
    assert chosen is None


def test_choose_test_property_prefers_exact_label_and_lowest_pid() -> None:
    chosen = choose_test_property(
        "P31",
        "item",
        property_label="instance of",
        property_datatypes={"P31": "url"},
        pid_map={},
        search_hits=[
            {"id": "P85140", "label": "instance of:jsiavb", "datatype": "wikibase-item"},
            {"id": "P82", "label": "instance of", "datatype": "wikibase-item"},
        ],
    )
    assert chosen == "P82"


def test_choose_test_property_same_id_fast_path() -> None:
    chosen = choose_test_property(
        "P217",
        "string",
        property_label="inventory number",
        property_datatypes={"P217": "string"},
        pid_map={},
        search_hits=[],
    )
    assert chosen == "P217"


def test_choose_test_item_never_uses_live_qid_without_map() -> None:
    chosen = choose_test_item(
        "Q9288",
        item_label="Hebrew",
        qid_map={},
        search_hits=[{"id": "Q9288", "label": "zEmCYgav", "datatype": ""}],
    )
    assert chosen is None


def test_choose_test_item_exact_label_match() -> None:
    chosen = choose_test_item(
        "Q9288",
        item_label="Hebrew",
        qid_map={},
        search_hits=[
            {"id": "Q999", "label": "Hebrew", "datatype": ""},
            {"id": "Q1000", "label": "hebrew", "datatype": ""},
        ],
    )
    assert chosen == "Q999"


def test_rewrite_item_with_maps() -> None:
    item = WikidataItem(
        statements=[
            WikidataStatement(property_id="P31", value="Q87167", value_type="item"),
            WikidataStatement(
                property_id="P1476",
                value="title",
                value_type="monolingualtext",
                references=[{"property": "P248", "value": "Q118384267", "type": "item"}],
            ),
        ],
    )
    rewritten = rewrite_item_with_maps(
        item,
        pid_map={
            pid_map_key("P31", "item"): "P82",
            pid_map_key("P1476", "monolingualtext"): "P77107",
            pid_map_key("P248", "item"): "P248",
        },
        qid_map={"Q87167": "Q500", "Q118384267": "Q501"},
    )
    assert rewritten.statements[0].property_id == "P82"
    assert rewritten.statements[0].value == "Q500"
    assert rewritten.statements[1].property_id == "P77107"
    assert rewritten.statements[1].references[0]["property"] == "P248"
    assert rewritten.statements[1].references[0]["value"] == "Q501"


def test_filter_after_rewrite_keeps_remapped_claims() -> None:
    item = WikidataItem(
        statements=[
            WikidataStatement(property_id="P77107", value="title", value_type="monolingualtext"),
            WikidataStatement(property_id="P82", value="Q500", value_type="item"),
        ],
    )
    filtered, skipped = filter_item_for_test_wiki(
        item,
        property_datatypes={
            "P77107": "monolingualtext",
            "P82": "wikibase-item",
        },
        existing_item_ids={"Q500"},
    )
    assert len(filtered.statements) == 2
    assert skipped == []


def test_filter_drops_unmapped_leftovers() -> None:
    item = WikidataItem(
        statements=[
            WikidataStatement(property_id="P217", value="F 1", value_type="string"),
            WikidataStatement(
                property_id="P1476", value="title", value_type="monolingualtext",
            ),
        ],
    )
    filtered, skipped = filter_item_for_test_wiki(
        item,
        property_datatypes={"P217": "string", "P1476": "globe-coordinate"},
        existing_item_ids=set(),
    )
    assert [s.property_id for s in filtered.statements] == ["P217"]
    assert any("P1476" in s for s in skipped)


def test_filter_drops_incompatible_qualifier() -> None:
    item = WikidataItem(
        statements=[
            WikidataStatement(
                property_id="P217",
                value="F 1",
                value_type="string",
                qualifiers=[
                    {"property": "P407", "value": "Q9288", "type": "item"},
                ],
            ),
        ],
    )
    filtered, skipped = filter_item_for_test_wiki(
        item,
        property_datatypes={"P217": "string", "P407": "wikibase-property"},
        existing_item_ids=set(),
    )
    assert len(filtered.statements) == 1
    assert filtered.statements[0].qualifiers == []
    assert any("P407" in s for s in skipped)


def test_collect_ids() -> None:
    item = WikidataItem(
        statements=[
            WikidataStatement(property_id="P31", value="Q87167", value_type="item"),
            WikidataStatement(
                property_id="P217",
                value="F 1",
                value_type="string",
                references=[{"property": "P854", "value": "https://x", "type": "url"}],
            ),
        ],
    )
    pids, qids = collect_test_wiki_ids(item)
    assert pids == ["P31", "P217", "P854"]
    assert qids == ["Q87167"]


def test_format_outcome_note() -> None:
    note = format_test_wiki_outcome_note(
        WikiTestAdaptResult(
            stats=WikiTestAdaptStats(properties_remapped=5, classes_created=2),
            skipped=["P999 missing on test"],
        ),
    )
    assert "remapped 5 properties" in note
    assert "remapped 2 classes" in note
    assert "skipped" not in note
    assert "W-182/W-183" in note


def test_upload_item_adapts_before_write(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
    )
    captured: dict[str, object] = {}

    class _FakeWbiItem:
        id = "Q9"

        def write(self, **kwargs):  # noqa: ANN003
            return self

    def _build(_item: WikidataItem):
        captured["pids"] = [s.property_id for s in _item.statements]
        return _FakeWbiItem(), len(_item.statements), list(captured["pids"])

    adapted = WikidataItem(
        local_id="QDraft_ms",
        entity_type="manuscript",
        labels={"en": "x"},
        statements=[
            WikidataStatement(property_id="P217", value="F 1", value_type="string"),
            WikidataStatement(
                property_id="P77107", value="title", value_type="monolingualtext",
            ),
        ],
    )

    def _adapt(_item: WikidataItem):
        return adapted, WikiTestAdaptResult(
            stats=WikiTestAdaptStats(properties_remapped=1),
            skipped=[],
        )

    monkeypatch.setattr(up, "_check_moratorium_for_live", lambda: None)
    monkeypatch.setattr(up, "_init_wbi", lambda: SimpleNamespace())
    monkeypatch.setattr(up, "_rate_limit", lambda: None)
    monkeypatch.setattr(up, "_build_wbi_item", _build)
    monkeypatch.setattr(up, "_assert_modifiable", lambda *_a, **_k: None)
    monkeypatch.setattr(up, "_adapt_item_for_test_wiki", _adapt)

    result = up.upload_item(
        WikidataItem(
            local_id="QDraft_ms",
            entity_type="manuscript",
            labels={"en": "x"},
            statements=[
                WikidataStatement(property_id="P217", value="F 1", value_type="string"),
                WikidataStatement(
                    property_id="P1476", value="title", value_type="monolingualtext",
                ),
            ],
        ),
    )
    assert result.status == "success"
    assert captured["pids"] == ["P217", "P77107"]
    assert "remapped 1 properties" in result.message
    assert "W-182/W-183" in result.message


def test_live_upload_does_not_strip_claims(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipeline:diagpasswordxxxxxxxx",
        is_test=False,
        allow_live=True,
    )
    captured: dict[str, object] = {}

    class _FakeWbiItem:
        id = "Q1"

        def write(self, **kwargs):  # noqa: ANN003
            return self

    def _build(_item: WikidataItem):
        captured["pids"] = [s.property_id for s in _item.statements]
        return _FakeWbiItem(), 2, ["P1476", "P31"]

    monkeypatch.setattr(up, "_check_moratorium_for_live", lambda: None)
    monkeypatch.setattr(up, "_init_wbi", lambda: SimpleNamespace())
    monkeypatch.setattr(up, "_rate_limit", lambda: None)
    monkeypatch.setattr(up, "_build_wbi_item", _build)
    monkeypatch.setattr(up, "_assert_modifiable", lambda *_a, **_k: None)

    def _boom(*_a, **_k):
        raise AssertionError("live must not adapt for test wiki")

    monkeypatch.setattr(up, "_adapt_item_for_test_wiki", _boom)

    result = up.upload_item(
        WikidataItem(
            local_id="x",
            entity_type="manuscript",
            labels={"en": "x"},
            statements=[
                WikidataStatement(
                    property_id="P1476", value="t", value_type="monolingualtext",
                ),
                WikidataStatement(property_id="P31", value="Q87167", value_type="item"),
            ],
        ),
    )
    assert result.status == "success"
    assert captured["pids"] == ["P1476", "P31"]
    assert "W-182" not in result.message


def test_bad_value_type_does_not_retry(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
    )
    calls = {"n": 0}

    class _FakeWbiItem:
        id = "Q9"

        def write(self, **kwargs):  # noqa: ANN003
            calls["n"] += 1
            raise RuntimeError("Bad value type monolingualtext, expected globecoordinate")

    monkeypatch.setattr(up, "_check_moratorium_for_live", lambda: None)
    monkeypatch.setattr(up, "_init_wbi", lambda: SimpleNamespace())
    monkeypatch.setattr(up, "_rate_limit", lambda: None)
    monkeypatch.setattr(
        up,
        "_adapt_item_for_test_wiki",
        lambda item: (item, WikiTestAdaptResult()),
    )
    monkeypatch.setattr(
        up, "_build_wbi_item", lambda _item: (_FakeWbiItem(), 1, ["P1476"]),
    )
    monkeypatch.setattr(up, "_assert_modifiable", lambda *_a, **_k: None)
    monkeypatch.setattr("converter.wikidata.uploader.time.sleep", lambda *_a: None)

    result = up.upload_item(
        WikidataItem(local_id="x", entity_type="manuscript", labels={"en": "x"}),
    )
    assert result.status == "failed"
    assert calls["n"] == 1
    assert "Bad claim datatype" in result.message


def test_ensure_test_maps_resolves_property_and_item(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
    )
    monkeypatch.setattr(up, "_init_wbi", lambda: SimpleNamespace())
    monkeypatch.setattr(up, "_login", SimpleNamespace())
    monkeypatch.setattr(
        up,
        "_ensure_test_property_datatypes",
        lambda pids: up._test_property_datatypes.update({
            "P1476": "globe-coordinate",
            "P77107": "monolingualtext",
        }),
    )
    monkeypatch.setattr(
        up,
        "_wbsearchentities",
        lambda search, *, entity_type, limit=8: (
            [{"id": "P77107", "label": "title", "datatype": "monolingualtext"}]
            if entity_type == "property" and search == "title"
            else [{"id": "Q999", "label": "Hebrew", "datatype": ""}]
            if entity_type == "item" and search == "Hebrew"
            else []
        ),
    )
    monkeypatch.setattr(up, "_create_test_property", lambda *_a, **_k: None)
    monkeypatch.setattr(up, "_create_test_item_stub", lambda *_a, **_k: None)
    monkeypatch.setattr(up, "_is_our_item", lambda qid: qid == "Q999")

    item = WikidataItem(
        statements=[
            WikidataStatement(
                property_id="P1476", value="t", value_type="monolingualtext",
            ),
            WikidataStatement(property_id="P407", value="Q9288", value_type="item"),
        ],
    )
    stats = up._ensure_test_maps_for_item(item)
    assert pid_map_lookup(up._test_pid_map, "P1476", "monolingualtext") == "P77107"
    assert up._test_qid_map["Q9288"] == "Q999"
    assert stats.properties_remapped == 1
    assert stats.classes_remapped == 1


def test_register_foreign_accept_ignored_on_test(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
    )
    monkeypatch.setattr(up, "_get_authenticated_user", lambda: "MHMBot")
    monkeypatch.setattr(up, "_get_first_revision_author", lambda _qid: "OtherUser")
    up.register_foreign_accept("Q42")
    assert "Q42" not in up._foreign_accept_qids
    assert up._is_our_item("Q42") is False


def test_ensure_test_maps_rejects_foreign_search_hit(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
    )
    monkeypatch.setattr(up, "_init_wbi", lambda: SimpleNamespace())
    monkeypatch.setattr(up, "_login", SimpleNamespace())
    monkeypatch.setattr(
        up,
        "_ensure_test_property_datatypes",
        lambda pids: None,
    )
    monkeypatch.setattr(
        up,
        "_wbsearchentities",
        lambda search, *, entity_type, limit=8: (
            [{"id": "Q999", "label": "Hebrew", "datatype": ""}]
            if entity_type == "item"
            else []
        ),
    )
    monkeypatch.setattr(up, "_is_our_item", lambda _qid: False)
    created: dict[str, str] = {}

    def _stub(label: str, live_qid: str) -> str:
        created["qid"] = "Q888"
        up._test_fresh_creates.add("Q888")
        return "Q888"

    monkeypatch.setattr(up, "_create_test_item_stub", _stub)

    stats = up._ensure_test_maps_for_item(
        WikidataItem(
            statements=[
                WikidataStatement(property_id="P407", value="Q9288", value_type="item"),
            ],
        ),
    )
    assert up._test_qid_map["Q9288"] == "Q888"
    assert stats.classes_created == 1
    assert "Q888" in up._test_stubs_we_created


def test_quantity_unit_mm_is_collected_and_remapped() -> None:
    item = WikidataItem(
        statements=[
            WikidataStatement(
                property_id="P2048",
                value="+100",
                value_type="quantity",
                unit="mm",
            ),
        ],
    )
    from converter.wikidata.test_wiki_compat import (  # noqa: PLC0415
        collect_test_wiki_ids,
        normalize_item_quantity_units,
        quantity_unit_to_live_qid,
        rewrite_item_with_maps,
    )

    assert quantity_unit_to_live_qid("mm") == "Q174789"
    normalized = normalize_item_quantity_units(item)
    _, qids = collect_test_wiki_ids(normalized)
    assert "Q174789" in qids
    rewritten = rewrite_item_with_maps(
        normalized,
        pid_map={pid_map_key("P2048", "quantity"): "P2048"},
        qid_map={"Q174789": "Q777"},
    )
    assert rewritten.statements[0].unit == "Q777"


def test_quantity_unit_qids_have_glosses() -> None:
    from converter.wikidata.property_labels import QID_LABELS  # noqa: PLC0415
    from converter.wikidata.test_wiki_compat import QUANTITY_UNIT_ALIASES  # noqa: PLC0415

    for live_qid in QUANTITY_UNIT_ALIASES.values():
        assert live_qid in QID_LABELS, live_qid


def test_quantity_unit_uri_uses_test_host(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
    )
    up._test_qid_map = {"Q174789": "Q888"}
    up._test_stubs_we_created = set()
    up._test_entity_exists = {"Q888": True}
    uri = up._quantity_unit_uri("mm")
    assert uri == "http://test.wikidata.org/entity/Q888"


def test_quantity_unit_unmapped_on_test_refuses_dimensionless() -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
    )
    up._test_qid_map = {}
    up._test_stubs_we_created = set()
    up._test_entity_exists = {}
    try:
        up._quantity_unit_uri("mm")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "W-186" in str(exc)


def test_leftover_snaks_refuse_degraded_write(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
    )
    written = {"n": 0}

    class _FakeWbiItem:
        id = "Q9"

        def write(self, **kwargs):  # noqa: ANN003
            written["n"] += 1
            return self

    monkeypatch.setattr(up, "_check_moratorium_for_live", lambda: None)
    monkeypatch.setattr(up, "_init_wbi", lambda: SimpleNamespace())
    monkeypatch.setattr(up, "_rate_limit", lambda: None)
    monkeypatch.setattr(
        up,
        "_adapt_item_for_test_wiki",
        lambda item: (
            item,
            WikiTestAdaptResult(skipped=["P1476 datatype monolingualtext != globecoordinate"]),
        ),
    )
    monkeypatch.setattr(
        up, "_build_wbi_item", lambda _item: (_FakeWbiItem(), 1, ["P1476"]),
    )
    monkeypatch.setattr(up, "_assert_modifiable", lambda *_a, **_k: None)

    result = up.upload_item(
        WikidataItem(local_id="x", entity_type="manuscript", labels={"en": "x"}),
    )
    assert result.status == "blocked"
    assert written["n"] == 0
    assert "W-186" in result.message
    assert "expert review" in result.message


def test_unmapped_quantity_unit_is_a_leftover() -> None:
    item = WikidataItem(
        statements=[
            WikidataStatement(
                property_id="P2048",
                value="+100",
                value_type="quantity",
                unit="Q174789",
            ),
        ],
    )
    filtered, skipped = filter_item_for_test_wiki(
        item,
        property_datatypes={"P2048": "quantity"},
        existing_item_ids=set(),
        live_static_qids={"Q174789"},
        allowed_item_ids=set(),
    )
    assert filtered.statements == []
    assert any("W-186" in s for s in skipped)


def test_illegal_live_entity_does_not_retry(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
    )
    calls = {"n": 0}

    class _FakeWbiItem:
        id = "Q9"

        def write(self, **kwargs):  # noqa: ANN003
            calls["n"] += 1
            raise RuntimeError(
                "Illegal value: http://www.wikidata.org/entity/Q174789"
            )

    monkeypatch.setattr(up, "_check_moratorium_for_live", lambda: None)
    monkeypatch.setattr(up, "_init_wbi", lambda: SimpleNamespace())
    monkeypatch.setattr(up, "_rate_limit", lambda: None)
    monkeypatch.setattr(
        up,
        "_adapt_item_for_test_wiki",
        lambda item: (item, WikiTestAdaptResult()),
    )
    monkeypatch.setattr(
        up, "_build_wbi_item", lambda _item: (_FakeWbiItem(), 1, ["P2048"]),
    )
    monkeypatch.setattr(up, "_assert_modifiable", lambda *_a, **_k: None)
    monkeypatch.setattr("converter.wikidata.uploader.time.sleep", lambda *_a: None)

    result = up.upload_item(
        WikidataItem(local_id="x", entity_type="manuscript", labels={"en": "x"}),
    )
    assert result.status == "failed"
    assert calls["n"] == 1
    assert "W-185" in result.message


def test_filter_drops_unmapped_live_qid_even_if_it_exists_on_test() -> None:
    item = WikidataItem(
        statements=[
            WikidataStatement(property_id="P82", value="Q9288", value_type="item"),
        ],
    )
    filtered, skipped = filter_item_for_test_wiki(
        item,
        property_datatypes={"P82": "wikibase-item"},
        existing_item_ids={"Q9288"},
        live_static_qids={"Q9288"},
        allowed_item_ids=set(),
    )
    assert filtered.statements == []
    assert any("W-183" in s for s in skipped)


def test_filter_keeps_remapped_live_class() -> None:
    item = WikidataItem(
        statements=[
            WikidataStatement(property_id="P82", value="Q999", value_type="item"),
        ],
    )
    filtered, skipped = filter_item_for_test_wiki(
        item,
        property_datatypes={"P82": "wikibase-item"},
        existing_item_ids={"Q999"},
        live_static_qids={"Q9288"},
        allowed_item_ids={"Q999"},
    )
    assert len(filtered.statements) == 1
    assert skipped == []


def test_foreign_existing_qid_creates_on_test(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
    )
    adapted = {"n": 0}
    written = {"n": 0}

    class _FakeWbiItem:
        id = "Q9"

        def write(self, **kwargs):  # noqa: ANN003
            written["n"] += 1
            return self

    def _adapt(item: WikidataItem):
        adapted["n"] += 1
        assert item.existing_qid is None
        return item, WikiTestAdaptResult()

    monkeypatch.setattr(up, "_check_moratorium_for_live", lambda: None)
    monkeypatch.setattr(up, "_init_wbi", lambda: SimpleNamespace())
    monkeypatch.setattr(up, "_is_our_item", lambda _qid: False)
    monkeypatch.setattr(up, "_adapt_item_for_test_wiki", _adapt)
    monkeypatch.setattr(up, "_rate_limit", lambda: None)
    monkeypatch.setattr(
        up, "_build_wbi_item", lambda _item: (_FakeWbiItem(), 1, ["P31"]),
    )
    monkeypatch.setattr(up, "_assert_modifiable", lambda *_a, **_k: None)

    result = up.upload_item(
        WikidataItem(
            local_id="x",
            entity_type="manuscript",
            existing_qid="Q209579",
            labels={"en": "x"},
            statements=[
                WikidataStatement(property_id="P31", value="Q87167", value_type="item"),
            ],
        ),
    )
    assert result.status == "success"
    assert adapted["n"] == 1
    assert written["n"] == 1


def test_foreign_existing_qid_skips_on_live(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipeline:diagpasswordxxxxxxxx",
        is_test=False,
        allow_live=True,
    )
    adapted = {"n": 0}

    def _adapt(item: WikidataItem):
        adapted["n"] += 1
        return item, WikiTestAdaptResult()

    monkeypatch.setattr(up, "_check_moratorium_for_live", lambda: None)
    monkeypatch.setattr(up, "_init_wbi", lambda: SimpleNamespace())
    monkeypatch.setattr(up, "_is_our_item", lambda _qid: False)
    monkeypatch.setattr(up, "_adapt_item_for_test_wiki", _adapt)

    result = up.upload_item(
        WikidataItem(
            local_id="x",
            entity_type="manuscript",
            existing_qid="Q209579",
            labels={"en": "x"},
        ),
    )
    assert result.status == "skipped"
    assert adapted["n"] == 0
    assert "not authored" in result.message


def test_parse_wbeditentity_conflict_id_from_html() -> None:
    body = {
        "error": {
            "messages": [
                {
                    "html": {
                        "*": 'Item [[Q248282|Q248282]] already has label "manuscript"',
                    },
                },
            ],
        },
    }
    assert parse_wbeditentity_conflict_id(body) == "Q248282"


def test_parse_wbeditentity_conflict_id_from_property_param() -> None:
    body = {
        "error": {
            "messages": [{"parameters": ["P99710"]}],
        },
    }
    assert parse_wbeditentity_conflict_id(body) == "P99710"


def test_pid_map_keys_separate_by_datatype() -> None:
    pid_map: dict[str, str] = {}
    pid_map_store(pid_map, "P100218", "string", "P9001")
    pid_map_store(pid_map, "P100218", "url", "P9002")
    assert pid_map_lookup(pid_map, "P100218", "string") == "P9001"
    assert pid_map_lookup(pid_map, "P100218", "url") == "P9002"


def test_mhm_stub_description_helpers() -> None:
    assert mhm_test_stub_description("Q87167") == "MHM test stub for live Q87167"
    assert description_is_mhm_stub_for(
        "MHM test stub for live Q87167",
        "Q87167",
    )


def test_mhm_stub_search_hit_usable_without_is_our_item(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
    )
    monkeypatch.setattr(
        up,
        "_item_english_description",
        lambda qid: mhm_test_stub_description("Q9288") if qid == "Q777" else "",
    )
    monkeypatch.setattr(up, "_is_our_item", lambda _qid: False)
    assert up._item_usable_as_test_reference("Q777", live_qid="Q9288") is True
    assert up._item_usable_as_test_reference("Q777", live_qid="Q87167") is False


def test_wbeditentity_new_adopts_conflict_id(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
    )

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {
                "error": {
                    "messages": [
                        {"parameters": ["Q248282"]},
                    ],
                },
            }

    class _Session:
        @staticmethod
        def post(*_a, **_k):
            return _Resp()

    monkeypatch.setattr(up, "_login", SimpleNamespace(get_session=lambda: _Session()))
    monkeypatch.setattr(up, "_get_csrf_token", lambda: "csrf")
    monkeypatch.setattr(up, "_rate_limit", lambda: None)
    assert up._wbeditentity_new(new="item", data={}) == "Q248282"


def test_p1680_has_property_label() -> None:
    from converter.wikidata.property_labels import PROPERTY_LABELS  # noqa: PLC0415

    assert PROPERTY_LABELS.get("P1680") == "subtitle"


def test_property_labels_cover_mapping_pids() -> None:
    import converter.wikidata.property_mapping as mapping  # noqa: PLC0415
    from converter.wikidata.property_labels import PROPERTY_LABELS  # noqa: PLC0415

    missing = [
        value
        for name, value in vars(mapping).items()
        if name.startswith("P_") and isinstance(value, str) and value.startswith("P")
        if value not in PROPERTY_LABELS
    ]
    assert missing == [], missing


def test_p1680_remaps_via_subtitle_gloss(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
    )
    monkeypatch.setattr(up, "_init_wbi", lambda: SimpleNamespace())
    monkeypatch.setattr(up, "_login", SimpleNamespace())
    monkeypatch.setattr(
        up,
        "_ensure_test_property_datatypes",
        lambda pids: up._test_property_datatypes.update({
            "P1680": "globe-coordinate",
            "P9001": "monolingualtext",
        }),
    )
    monkeypatch.setattr(
        up,
        "_wbsearchentities",
        lambda search, *, entity_type, limit=8: (
            [{"id": "P9001", "label": "subtitle", "datatype": "monolingualtext"}]
            if entity_type == "property" and search == "subtitle"
            else []
        ),
    )
    monkeypatch.setattr(up, "_create_test_property", lambda *_a, **_k: None)
    stats = up._ensure_test_maps_for_item(
        WikidataItem(
            statements=[
                WikidataStatement(
                    property_id="P1680", value="ותרגום", value_type="monolingualtext",
                ),
            ],
        ),
    )
    assert pid_map_lookup(up._test_pid_map, "P1680", "monolingualtext") == "P9001"
    assert stats.properties_remapped == 1


def test_wbeditentity_researches_when_conflict_unparsed(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
    )

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {"error": {"info": "label conflict without id"}}

    class _Session:
        @staticmethod
        def post(*_a, **_k):
            return _Resp()

    monkeypatch.setattr(up, "_login", SimpleNamespace(get_session=lambda: _Session()))
    monkeypatch.setattr(up, "_get_csrf_token", lambda: "csrf")
    monkeypatch.setattr(up, "_rate_limit", lambda: None)
    monkeypatch.setattr(
        up,
        "_wbsearchentities",
        lambda search, *, entity_type, limit=8: (
            [{"id": "Q248282", "label": "manuscript", "datatype": ""}]
            if entity_type == "item" and search == "manuscript"
            else []
        ),
    )
    data = {"labels": {"en": {"language": "en", "value": "manuscript"}}}
    assert up._wbeditentity_new(new="item", data=data) == "Q248282"


def test_warm_test_maps_for_items_fills_session_maps(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
    )
    monkeypatch.setattr(up, "_init_wbi", lambda: SimpleNamespace())
    monkeypatch.setattr(up, "_login", SimpleNamespace())
    called: list[str] = []

    def _ensure(item: WikidataItem):
        called.append(item.local_id)
        return WikiTestAdaptStats()

    monkeypatch.setattr(up, "_ensure_test_maps_for_item", _ensure)
    up.warm_test_maps_for_items([
        WikidataItem(local_id="a", entity_type="manuscript"),
        WikidataItem(local_id="b", entity_type="work"),
    ])
    assert called == ["a", "b"]


def test_test_sparql_false_falls_back_to_action_api(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
    )
    monkeypatch.setattr(up, "_get_authenticated_user", lambda: "MHMBot")
    monkeypatch.setattr(up, "_get_first_revision_author", lambda _qid: "MHMBot")
    monkeypatch.setattr(up, "_user_created_via_contribs", lambda *_a: True)
    monkeypatch.setattr(up, "_item_exists_on_wikidata_sparql", lambda _qid: False)
    monkeypatch.setattr(
        up,
        "_wbgetentities",
        lambda ids, *, props: {"Q248051": {"id": "Q248051"}},
    )
    assert up._is_our_item("Q248051") is True


def test_upload_all_does_not_wire_skipped_foreign_qid_on_test(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
    )
    written: list[str] = []

    def _upload(item: WikidataItem):
        person_val = next(
            (s.value for s in item.statements if s.property_id == "P50"),
            None,
        )
        if item.local_id == "person:a":
            return SimpleNamespace(
                local_id=item.local_id, qid="Q209579", status="skipped",
                message="foreign", added_properties=[],
            )
        written.append(str(person_val))
        return SimpleNamespace(
            local_id=item.local_id, qid="Q1", status="success",
            message="created", added_properties=[],
        )

    monkeypatch.setattr(up, "upload_item", _upload)
    monkeypatch.setattr(up, "warm_test_maps_for_items", lambda _items: None)
    person = WikidataItem(local_id="person:a", entity_type="person", labels={"en": "p"})
    ms = WikidataItem(
        local_id="ms:a",
        entity_type="manuscript",
        labels={"en": "m"},
        statements=[
            WikidataStatement(property_id="P50", value="__LOCAL:person:a", value_type="item"),
        ],
    )
    up.upload_all([person, ms])
    assert written == ["__LOCAL:person:a"]


def test_upload_all_wires_skipped_foreign_qid_on_live(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipeline:diagpasswordxxxxxxxx",
        is_test=False,
        allow_live=True,
    )
    written: list[str] = []

    def _upload(item: WikidataItem):
        person_val = next(
            (s.value for s in item.statements if s.property_id == "P50"),
            None,
        )
        if item.local_id == "person:a":
            return SimpleNamespace(
                local_id=item.local_id, qid="Q209579", status="skipped",
                message="foreign", added_properties=[],
            )
        written.append(str(person_val))
        return SimpleNamespace(
            local_id=item.local_id, qid="Q1", status="success",
            message="created", added_properties=[],
        )

    monkeypatch.setattr(up, "upload_item", _upload)
    person = WikidataItem(local_id="person:a", entity_type="person", labels={"en": "p"})
    ms = WikidataItem(
        local_id="ms:a",
        entity_type="manuscript",
        labels={"en": "m"},
        statements=[
            WikidataStatement(property_id="P50", value="__LOCAL:person:a", value_type="item"),
        ],
    )
    up.upload_all([person, ms])
    assert written == ["Q209579"]
