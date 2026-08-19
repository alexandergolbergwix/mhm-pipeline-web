"""Rule W-179 — upload jobs must reuse one Wikidata login session."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.pipeline.wikidata_upload import _is_auth_failure_message, _upload_sync
from converter.wikidata.uploader import WikidataUploader


def test_auth_failure_detector() -> None:
    assert _is_auth_failure_message(
        "Login failed. Reason: 'You have made too many recent login attempts.'"
    )
    assert _is_auth_failure_message("Incorrect username or password entered.")
    assert _is_auth_failure_message("MediaWiki permissiondenied on test.wikidata.org")
    assert _is_auth_failure_message("You are no longer logged in, so this action could not be completed.")
    assert _is_auth_failure_message("Your IP address has been blocked globally")
    assert not _is_auth_failure_message("Blocked by validator (ERROR: x)")


def test_shared_uploader_is_reused_not_recreated(monkeypatch) -> None:
    shared = MagicMock()
    shared.upload_item.return_value = SimpleNamespace(
        qid="Q1", status="success", message="created", added_properties=[],
    )
    created: list[object] = []

    class _Boom(WikidataUploader):  # type: ignore[misc]
        def __init__(self, *a, **k):  # noqa: ANN002, ANN003
            created.append(object())
            raise AssertionError("must not construct a second uploader")

    monkeypatch.setattr(
        "converter.wikidata.uploader.WikidataUploader",
        _Boom,
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_upload._make_reconciler",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_upload._prepare_for_upload",
        lambda items, reconciler, **kwargs: [
            SimpleNamespace(
                item=items[0],
                local_id="QDraft_1",
                label="x",
                entity_type="person",
                existing_qid=None,
                method="none",
                blocked=False,
                block_status="",
                block_message="",
                had_builder_qid=False,
                adopt_candidate=False,
                allow_foreign_modify=False,
                ownership="absent",
            )
        ],
    )

    item = SimpleNamespace(local_id="QDraft_1", entity_type="person", existing_qid=None)
    out = _upload_sync(
        [item],
        token="User@Bot:secret",
        dry_run=False,
        ledger={},
        ledger_ns="wikidata",
        is_test=True,
        allow_live=False,
        uploader=shared,
    )
    assert created == []
    assert shared.upload_item.call_count == 1
    assert out[0].status == "created"


def test_ensure_authenticated_is_idempotent(monkeypatch) -> None:
    calls = {"n": 0}

    def _init(self):  # noqa: ANN001
        if self._wbi is not None:
            return self._wbi
        calls["n"] += 1
        self._wbi = object()
        return self._wbi

    monkeypatch.setattr(WikidataUploader, "_init_wbi", _init)
    monkeypatch.setattr(
        WikidataUploader,
        "assert_write_capability",
        lambda self: None,
    )
    up = WikidataUploader.__new__(WikidataUploader)
    up._token = "User@Bot:x"
    up._is_test = True
    up._wbi = None
    up._login = None
    up.ensure_authenticated()
    up.ensure_authenticated()
    assert calls["n"] == 1
