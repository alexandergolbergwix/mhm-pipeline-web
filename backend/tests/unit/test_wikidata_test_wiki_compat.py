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
    expected_wikibase_datatype,
    filter_item_for_test_wiki,
    format_test_wiki_outcome_note,
    item_target_qid,
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
        pid_map={"P31": "P82", "P1476": "P77107", "P248": "P248"},
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
    assert "skipped 1 snaks" in note
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
    assert "not retried" in result.message


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
    assert up._test_pid_map["P1476"] == "P77107"
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


def test_foreign_existing_qid_skips_before_test_adapt(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
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
