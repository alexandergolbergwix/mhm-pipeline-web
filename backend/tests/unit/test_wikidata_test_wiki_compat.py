"""test.wikidata.org smoke-path claim filter (Rule W-182)."""

from __future__ import annotations

from types import SimpleNamespace

from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.test_wiki_compat import (
    collect_test_wiki_ids,
    expected_wikibase_datatype,
    filter_item_for_test_wiki,
    item_target_qid,
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


def test_filter_keeps_matching_string_drops_title_and_missing_qid() -> None:
    item = WikidataItem(
        local_id="QDraft_ms",
        entity_type="manuscript",
        labels={"en": "NLI, F 1"},
        statements=[
            WikidataStatement(property_id="P217", value="F 1", value_type="string"),
            WikidataStatement(
                property_id="P1476", value="משנה תורה", value_type="monolingualtext",
            ),
            WikidataStatement(property_id="P31", value="Q87167", value_type="item"),
            WikidataStatement(property_id="P50", value="Q42", value_type="item"),
            WikidataStatement(property_id="P195", value="Q24568958", value_type="item"),
        ],
    )
    filtered, skipped = filter_item_for_test_wiki(
        item,
        property_datatypes={
            "P217": "string",
            "P1476": "globe-coordinate",
            "P31": "url",
            "P50": "wikibase-item",
            "P195": "wikibase-item",
        },
        existing_item_ids={"Q42"},
    )
    pids = [s.property_id for s in filtered.statements]
    assert pids == ["P217", "P50"]
    assert filtered.labels == {"en": "NLI, F 1"}
    assert any("P1476" in s for s in skipped)
    assert any("P31" in s and "url" in s for s in skipped)
    assert any("Q24568958" in s for s in skipped)


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


def test_upload_item_filters_before_write(monkeypatch) -> None:
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

    monkeypatch.setattr(up, "_check_moratorium_for_live", lambda: None)
    monkeypatch.setattr(up, "_init_wbi", lambda: SimpleNamespace())
    monkeypatch.setattr(up, "_rate_limit", lambda: None)
    monkeypatch.setattr(up, "_build_wbi_item", _build)
    monkeypatch.setattr(up, "_assert_modifiable", lambda *_a, **_k: None)
    monkeypatch.setattr(
        up,
        "_wbgetentities",
        lambda ids, *, props: {
            "P217": {"datatype": "string"},
            "P1476": {"datatype": "globe-coordinate"},
        },
    )

    item = WikidataItem(
        local_id="QDraft_ms",
        entity_type="manuscript",
        labels={"en": "x"},
        statements=[
            WikidataStatement(property_id="P217", value="F 1", value_type="string"),
            WikidataStatement(
                property_id="P1476", value="title", value_type="monolingualtext",
            ),
        ],
    )
    result = up.upload_item(item)
    assert result.status == "success"
    assert captured["pids"] == ["P217"]
    assert "skipped 1" in result.message
    assert "W-182" in result.message


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
        raise AssertionError("live must not fetch test datatypes")

    monkeypatch.setattr(up, "_wbgetentities", _boom)

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
    monkeypatch.setattr(up, "_adapt_item_for_test_wiki", lambda item: (item, []))
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
