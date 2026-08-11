"""WikidataUploader.write must use WBI ``is_bot``, not ``bot`` (Rule W-180)."""

from __future__ import annotations

from types import SimpleNamespace

from converter.wikidata.item_models import WikidataItem
from converter.wikidata.uploader import WikidataUploader


def test_upload_item_passes_is_bot_not_bot(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
    )
    captured: dict[str, object] = {}

    class _FakeWbiItem:
        id = "Q999"

        def write(self, **kwargs):  # noqa: ANN003
            captured.clear()
            captured.update(kwargs)
            return self

    monkeypatch.setattr(up, "_check_moratorium_for_live", lambda: None)
    monkeypatch.setattr(up, "_init_wbi", lambda: SimpleNamespace())
    monkeypatch.setattr(up, "_rate_limit", lambda: None)
    monkeypatch.setattr(
        up,
        "_build_wbi_item",
        lambda _item: (_FakeWbiItem(), 1, ["P31"]),
    )
    monkeypatch.setattr(up, "_assert_modifiable", lambda *_a, **_k: None)

    item = WikidataItem(
        local_id="QDraft_test",
        entity_type="manuscript",
        labels={"en": "test"},
    )
    result = up.upload_item(item)

    assert result.status == "success"
    assert "bot" not in captured
    assert captured.get("is_bot") is True
    assert "summary" in captured
