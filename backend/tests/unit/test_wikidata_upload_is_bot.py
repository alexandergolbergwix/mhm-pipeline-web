"""WikidataUploader.write must use WBI ``is_bot``, never bare ``bot`` (W-180/181)."""

from __future__ import annotations

from types import SimpleNamespace

from converter.wikidata.item_models import WikidataItem
from converter.wikidata.uploader import WikidataUploader


def test_upload_item_default_is_bot_false(monkeypatch) -> None:
    monkeypatch.delenv("WIKIDATA_MARK_AS_BOT", raising=False)
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
    assert captured.get("is_bot") is False


def test_upload_item_mark_as_bot_true(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
        mark_as_bot=True,
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

    result = up.upload_item(
        WikidataItem(local_id="x", entity_type="person", labels={"en": "x"}),
    )
    assert result.status == "success"
    assert captured.get("is_bot") is True


def test_bot_right_error_does_not_retry(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
        mark_as_bot=True,
    )
    calls = {"n": 0}

    class _FakeWbiItem:
        id = "Q999"

        def write(self, **kwargs):  # noqa: ANN003
            calls["n"] += 1
            raise RuntimeError(
                'You do not have the "bot" right, so the action could not be completed.'
            )

    monkeypatch.setattr(up, "_check_moratorium_for_live", lambda: None)
    monkeypatch.setattr(up, "_init_wbi", lambda: SimpleNamespace())
    monkeypatch.setattr(up, "_rate_limit", lambda: None)
    monkeypatch.setattr(
        up,
        "_build_wbi_item",
        lambda _item: (_FakeWbiItem(), 1, ["P31"]),
    )
    monkeypatch.setattr(up, "_assert_modifiable", lambda *_a, **_k: None)
    monkeypatch.setattr("converter.wikidata.uploader.time.sleep", lambda *_a: None)

    result = up.upload_item(
        WikidataItem(local_id="x", entity_type="person", labels={"en": "x"}),
    )
    assert result.status == "failed"
    assert calls["n"] == 1
    assert "bot" in result.message.lower()


def test_permissiondenied_does_not_retry(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
    )
    calls = {"n": 0}

    class _FakeWbiItem:
        id = "Q999"

        def write(self, **kwargs):  # noqa: ANN003
            calls["n"] += 1
            err = RuntimeError(
                "You do not have the permissions needed to carry out this action."
            )
            err.code = "permissiondenied"  # type: ignore[attr-defined]
            raise err

    monkeypatch.setattr(up, "_check_moratorium_for_live", lambda: None)
    monkeypatch.setattr(up, "_init_wbi", lambda: SimpleNamespace())
    monkeypatch.setattr(up, "_rate_limit", lambda: None)
    monkeypatch.setattr(
        up,
        "_build_wbi_item",
        lambda _item: (_FakeWbiItem(), 1, ["P31"]),
    )
    monkeypatch.setattr(up, "_assert_modifiable", lambda *_a, **_k: None)
    monkeypatch.setattr("converter.wikidata.uploader.time.sleep", lambda *_a: None)

    result = up.upload_item(
        WikidataItem(local_id="x", entity_type="work", labels={"en": "x"}),
    )
    assert result.status == "failed"
    assert calls["n"] == 1
    assert "permissiondenied" in result.message.lower()
    assert "Create, edit, and move pages" in result.message


def test_session_death_does_not_retry(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
    )
    calls = {"n": 0}

    class _FakeWbiItem:
        id = "Q999"

        def write(self, **kwargs):  # noqa: ANN003
            calls["n"] += 1
            raise RuntimeError("You are no longer logged in, so this action could not be completed.")

    monkeypatch.setattr(up, "_check_moratorium_for_live", lambda: None)
    monkeypatch.setattr(up, "_init_wbi", lambda: SimpleNamespace())
    monkeypatch.setattr(up, "_rate_limit", lambda: None)
    monkeypatch.setattr(
        up,
        "_build_wbi_item",
        lambda _item: (_FakeWbiItem(), 1, ["P31"]),
    )
    monkeypatch.setattr(up, "_assert_modifiable", lambda *_a, **_k: None)
    monkeypatch.setattr("converter.wikidata.uploader.time.sleep", lambda *_a: None)

    result = up.upload_item(
        WikidataItem(local_id="x", entity_type="person", labels={"en": "x"}),
    )
    assert result.status == "failed"
    assert calls["n"] == 1
    assert "Session expired" in result.message


def test_assert_write_capability_rejects_missing_createpage(monkeypatch) -> None:
    up = WikidataUploader(
        token="Alexander Goldberg IL@MHMPipelineTest:diagpasswordxxxxxxxx",
        is_test=True,
    )
    monkeypatch.setattr(
        up,
        "_query_userinfo_rights",
        lambda: {"name": "Alexander Goldberg IL", "rights": ["read", "edit"]},
    )
    try:
        up.assert_write_capability()
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "createpage" in str(exc)
        assert "Special:BotPasswords" in str(exc)
