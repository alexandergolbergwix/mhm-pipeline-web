"""The uploader must separate creator ownership from QID-bound approval."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import requests

from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.uploader import UnauthorisedModificationError, WikidataUploader


@pytest.fixture
def uploader(monkeypatch: pytest.MonkeyPatch) -> WikidataUploader:
    up = WikidataUploader(token="Fixture@Bot:not-a-credential", allow_live=True)
    monkeypatch.setattr(up, "_get_authenticated_user", lambda: "Project")
    monkeypatch.setattr(up, "_get_first_revision_author", lambda qid: "Other")
    monkeypatch.setattr(up, "_item_exists_on_wikidata_sparql", lambda qid: True)
    monkeypatch.setattr(up, "_init_wbi", lambda: SimpleNamespace())
    monkeypatch.setattr(up, "_rate_limit", lambda: None)
    monkeypatch.setattr(up, "_bot_excluded", lambda qid: False)
    up._q3959_cache["Q123"] = frozenset({"9901"})
    return up


def manuscript(qid: str = "Q123") -> WikidataItem:
    return WikidataItem(
        local_id="ms1", entity_type="manuscript", existing_qid=qid,
        statements=[WikidataStatement("P3959", "9901", "external-id")],
    )


def test_matching_p3959_never_proves_creator_ownership(uploader: WikidataUploader) -> None:
    assert not uploader._is_our_item("Q123", our_p3959="9901")
    with pytest.raises(UnauthorisedModificationError):
        uploader._assert_modifiable("Q123", "test", our_p3959="9901")


def test_approval_does_not_change_ownership_or_approve_another_qid(
    uploader: WikidataUploader,
) -> None:
    uploader.register_foreign_accept("Q123")
    assert not uploader._is_our_item("Q123", our_p3959="9901")
    uploader._assert_modifiable("Q123", "test")
    with pytest.raises(UnauthorisedModificationError):
        uploader._assert_modifiable("Q124", "test")
    assert uploader._is_our_item_cache.get("Q123") is False


def test_direct_upload_blocks_foreign_p3959_and_accepts_exact_qid(
    uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[str] = []

    class FakeItem:
        id = "Q123"

        def write(self, **kwargs: object) -> object:
            writes.append(self.id)
            return self

    monkeypatch.setattr(uploader, "_build_wbi_item", lambda item: (FakeItem(), 1, ["P3959"]))
    assert uploader.upload_item(manuscript()).status == "skipped"
    assert writes == []
    uploader.register_foreign_accept("Q124")
    assert uploader.upload_item(manuscript()).status == "skipped"
    assert writes == []
    uploader.register_foreign_accept("Q123")
    assert uploader.upload_item(manuscript()).status == "updated"
    assert writes == ["Q123"]


def test_creator_owned_item_does_not_require_foreign_approval(
    uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(uploader, "_get_first_revision_author", lambda qid: "Project")
    monkeypatch.setattr(uploader, "_user_created_via_contribs", lambda qid, user: True)
    assert uploader._is_our_item("Q123")
    uploader._assert_modifiable("Q123", "pass2")
    assert not uploader._foreign_accept_qids


def test_new_item_can_be_created_without_a_target_qid(
    uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeItem:
        id: str | None = None

        def write(self, **kwargs: object) -> object:
            self.id = "Q123"
            return self

    monkeypatch.setattr(uploader, "_build_wbi_item", lambda item: (FakeItem(), 1, ["P3959"]))
    item = manuscript()
    item.existing_qid = None
    result = uploader.upload_item(item)
    assert result.status == "success"
    assert result.qid == "Q123"


def test_real_wbi_new_item_has_no_existing_target(
    uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wikibaseintegrator import WikibaseIntegrator

    monkeypatch.setattr(uploader, "_init_wbi", lambda: WikibaseIntegrator())
    item = manuscript()
    item.existing_qid = None
    entity, count, _ = uploader._build_wbi_item(item)
    assert entity.id is None
    assert count == 1


def test_real_wbi_create_writes_without_id_and_reads_assigned_qid(
    uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wikibaseintegrator import WikibaseIntegrator

    calls: list[dict[str, object]] = []

    def fake_edit_entity(**kwargs: object) -> dict:
        calls.append(kwargs)
        data = kwargs["data"]
        assert isinstance(data, dict)
        assert kwargs["id"] is None
        assert "id" not in data
        claims = data["claims"]
        assert isinstance(claims, dict)
        response_claims = {
            pid: [{**claim, "id": "Q123$fixture"} for claim in statements]
            for pid, statements in claims.items()
        }
        return {"entity": {**data, "claims": response_claims, "id": "Q123", "lastrevid": 1}}

    monkeypatch.setattr(uploader, "_init_wbi", lambda: WikibaseIntegrator())
    monkeypatch.setattr("wikibaseintegrator.entities.baseentity.edit_entity", fake_edit_entity)
    item = manuscript()
    item.existing_qid = None
    result = uploader.upload_item(item)
    assert result.status == "success"
    assert result.qid == "Q123"
    assert len(calls) == 1


def test_write_rejects_a_different_actual_target(
    uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[str] = []

    class FakeItem:
        id = "Q124"

        def write(self, **kwargs: object) -> object:
            writes.append(self.id)
            return self

    uploader.register_foreign_accept("Q123")
    monkeypatch.setattr(uploader, "_build_wbi_item", lambda item: (FakeItem(), 1, ["P3959"]))
    assert uploader.upload_item(manuscript()).status in {"blocked", "skipped"}
    assert writes == []


def test_create_rejects_an_existing_actual_target(
    uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[str] = []

    class FakeItem:
        id = "Q123"

        def write(self, **kwargs: object) -> object:
            writes.append(self.id)
            return self

    monkeypatch.setattr(uploader, "_build_wbi_item", lambda item: (FakeItem(), 1, ["P3959"]))
    item = manuscript()
    item.existing_qid = None
    assert uploader.upload_item(item).status == "blocked"
    assert writes == []


def test_type_error_retry_refuses_a_changed_target(
    uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[str] = []

    class FakeItem:
        id = "Q123"

        def write(self, **kwargs: object) -> object:
            writes.append(self.id)
            self.id = "Q124"
            raise TypeError("write failed")

    uploader.register_foreign_accept("Q123")
    monkeypatch.setattr(uploader, "_build_wbi_item", lambda item: (FakeItem(), 1, ["P3959"]))
    result = uploader.upload_item(manuscript())
    assert result.status == "failed"
    assert "outcome unknown" in result.message.lower()
    assert writes == ["Q123"]


def test_build_rejects_a_prefetched_entity_with_a_different_qid(
    uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wikibaseintegrator import WikibaseIntegrator

    uploader.register_foreign_accept("Q123")
    uploader._prefetched_entities["Q123"] = {
        "id": "Q124", "type": "item", "lastrevid": 1,
        "labels": {"en": {"language": "en", "value": "Wrong target"}},
        "descriptions": {}, "aliases": {}, "claims": {}, "sitelinks": {},
    }
    monkeypatch.setattr(uploader, "_init_wbi", lambda: WikibaseIntegrator())
    result = uploader.upload_item(manuscript())
    assert result.status == "blocked"
    assert "TARGET" in result.message
    assert "Q123" not in uploader._existing_label_snapshot
    uploader._prefetched_entities["Q123"] = {
        "id": "Q123", "type": "item", "lastrevid": 1,
        "labels": {"en": {"language": "en", "value": "Correct target"}},
        "descriptions": {}, "aliases": {}, "claims": {}, "sitelinks": {},
    }
    uploader.register_foreign_accept("Q123")
    uploader._build_wbi_item(manuscript())
    assert uploader._existing_label_snapshot["Q123"] == {"en": "Correct target"}


def test_approved_foreign_item_keeps_existing_metadata(
    uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wikibaseintegrator import WikibaseIntegrator

    uploader.register_foreign_accept("Q123")
    uploader._prefetched_entities["Q123"] = {
        "id": "Q123", "type": "item", "lastrevid": 1,
        "labels": {"en": {"language": "en", "value": "Existing label"}},
        "descriptions": {"en": {"language": "en", "value": "Existing description"}},
        "aliases": {}, "claims": {}, "sitelinks": {},
    }
    monkeypatch.setattr(uploader, "_init_wbi", lambda: WikibaseIntegrator())
    item = manuscript()
    item.labels = {"en": "Proposed label"}
    item.descriptions = {"en": "Proposed description"}
    entity, _, _ = uploader._build_wbi_item(item)
    assert entity.labels.get("en").value == "Existing label"
    assert entity.descriptions.get("en").value == "Existing description"
    assert uploader._is_our_item_cache.get("Q123") is not True


def test_deferred_link_update_requires_approval_for_its_source_qid(
    uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[str] = []

    class FakeItem:
        id = "Q123"

        def write(self, **kwargs: object) -> object:
            writes.append(self.id)
            return self

    monkeypatch.setattr(uploader, "_build_wbi_item", lambda item: (FakeItem(), 1, ["P1574"]))
    item = WikidataItem(
        local_id="ms1", entity_type="manuscript", existing_qid="Q123",
        statements=[WikidataStatement("P1574", "__LOCAL:work1", "item")],
    )
    uploader.register_foreign_accept("Q124")
    assert uploader.upload_item(item, created_qids={"work1": "Q456"}).status == "skipped"
    assert writes == []
    uploader.register_foreign_accept("Q123")
    assert uploader.upload_item(item, created_qids={"work1": "Q456"}).status == "updated"
    assert writes == ["Q123"]


def test_cache_warm_does_not_trust_old_creation_contribution(
    uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"query": {"usercontribs": [{"title": "Q123"}]}}

    monkeypatch.setattr(
        uploader, "_login", SimpleNamespace(get_session=lambda: SimpleNamespace(
            get=lambda *args, **kwargs: Response(),
        )),
    )
    uploader.warm_ownership_cache(["Q123"], {"Q123": {"labels": {"en": "Example"}}})
    assert not uploader._is_our_item("Q123")


def test_cache_share_refuses_a_different_account(uploader: WikidataUploader) -> None:
    other = WikidataUploader(token="Other@Bot:not-a-credential", allow_live=True)
    uploader._authenticated_user = "Project"
    other._authenticated_user = "Other"
    uploader._is_our_item_cache["Q123"] = True
    with pytest.raises(ValueError):
        other.share_caches_from(uploader)
    assert other._authenticated_user == "Other"


def test_cache_share_allows_the_same_account_on_the_same_wiki(
    uploader: WikidataUploader,
) -> None:
    other = WikidataUploader(token="Fixture@Bot:not-a-credential", allow_live=True)
    uploader._authenticated_user = "Project"
    other._authenticated_user = "Project"
    uploader.register_foreign_accept("Q123")
    uploader._is_our_item_cache["Q456"] = True
    other.share_caches_from(uploader)
    other._assert_modifiable("Q456", "pass2")
    assert other._foreign_accept_qids is not uploader._foreign_accept_qids


def test_test_wiki_rejects_foreign_accept_even_if_the_set_is_populated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    up = WikidataUploader(token="Fixture@Bot:not-a-credential", is_test=True)
    up._foreign_accept_qids.add("Q123")
    monkeypatch.setattr(up, "_get_authenticated_user", lambda: "Project")
    monkeypatch.setattr(up, "_get_first_revision_author", lambda qid: "Other")
    with pytest.raises(UnauthorisedModificationError):
        up._assert_modifiable("Q123", "test")


@pytest.mark.parametrize("is_create", [True, False])
@pytest.mark.parametrize("error", [
    requests.exceptions.ReadTimeout("response timed out"),
    requests.exceptions.ConnectionError("connection lost"),
    TypeError("response parse failed"),
    RuntimeError('Item [[Q999|Q999]] already has label "Example"'),
])
def test_ambiguous_write_is_not_retried(
    uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch,
    is_create: bool, error: Exception,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeItem:
        id = None if is_create else "Q123"

        def write(self, **kwargs: object) -> object:
            calls.append(kwargs)
            raise error

    monkeypatch.setattr(uploader, "_build_wbi_item", lambda item: (FakeItem(), 1, ["P3959"]))
    monkeypatch.setattr("converter.wikidata.uploader.time.sleep", lambda seconds: None)
    item = manuscript()
    if is_create:
        item.existing_qid = None
    else:
        uploader.register_foreign_accept("Q123")
    result = uploader.upload_item(item)
    assert result.status == "failed"
    assert "outcome unknown" in result.message.lower()
    assert len(calls) == 1
    assert calls[0]["max_retries"] == 1


@pytest.mark.parametrize("failure", ["timeout", "connection", "server_error"])
def test_real_wbi_http_helper_does_not_repeat_a_write(
    uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    from wikibaseintegrator import WikibaseIntegrator
    from wikibaseintegrator.wbi_helpers import mediawiki_api_call

    calls: list[str] = []

    def request(**kwargs: object) -> object:
        calls.append("post")
        if failure == "timeout":
            raise requests.exceptions.ReadTimeout("response timed out")
        if failure == "connection":
            raise requests.exceptions.ConnectionError("connection lost")
        return SimpleNamespace(status_code=503)

    def edit_entity(**kwargs: object) -> dict:
        return mediawiki_api_call(
            "POST", session=SimpleNamespace(request=request),
            max_retries=kwargs["max_retries"], retry_after=kwargs["retry_after"],
        )

    monkeypatch.setattr(uploader, "_init_wbi", lambda: WikibaseIntegrator())
    monkeypatch.setattr("wikibaseintegrator.entities.baseentity.edit_entity", edit_entity)
    item = manuscript()
    item.existing_qid = None
    result = uploader.upload_item(item)
    assert result.status == "failed"
    assert "outcome unknown" in result.message.lower()
    assert calls == ["post"]


def test_foreign_approval_is_consumed_after_one_upload(
    uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[str] = []

    class FakeItem:
        id = "Q123"

        def write(self, **kwargs: object) -> object:
            writes.append(self.id)
            return self

    monkeypatch.setattr(uploader, "_build_wbi_item", lambda item: (FakeItem(), 1, ["P3959"]))
    uploader.register_foreign_accept("Q123")
    assert uploader.upload_item(manuscript()).status == "updated"
    assert uploader.upload_item(manuscript()).status == "skipped"
    assert writes == ["Q123"]


def test_foreign_approval_is_cleared_after_early_failure(uploader: WikidataUploader) -> None:
    uploader.register_foreign_accept("Q123")
    item = manuscript()
    item.statements.append(WikidataStatement("P1574", "__LOCAL:missing", "item"))
    assert uploader.upload_item(item).status == "blocked"
    assert uploader._foreign_accept_qids == set()


def test_foreign_approval_is_cleared_after_unexpected_exception(
    uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail() -> None:
        raise RuntimeError("login failed")

    monkeypatch.setattr(uploader, "_init_wbi", fail)
    uploader.register_foreign_accept("Q123")
    with pytest.raises(RuntimeError, match="login failed"):
        uploader.upload_item(manuscript())
    assert not uploader._foreign_accept_qids


def test_shared_caches_do_not_share_foreign_approvals(uploader: WikidataUploader) -> None:
    other = WikidataUploader(token="Fixture@Bot:not-a-credential", allow_live=True)
    uploader._authenticated_user = "Project"
    other._authenticated_user = "Project"
    uploader.register_foreign_accept("Q123")
    other.share_caches_from(uploader)
    assert "Q123" not in other._foreign_accept_qids


def test_foreign_approval_is_not_visible_to_another_thread(
    uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeItem:
        id = "Q123"

        def write(self, **kwargs: object) -> object:
            return self

    monkeypatch.setattr(uploader, "_build_wbi_item", lambda item: (FakeItem(), 1, ["P3959"]))
    uploader.register_foreign_accept("Q123")
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(uploader.upload_item, manuscript()).result()
    assert result.status == "skipped"
    assert uploader.upload_item(manuscript()).status == "updated"


def test_foreign_approval_does_not_retry_a_confirmed_rejection(
    uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wikibaseintegrator.wbi_exceptions import MWApiError

    calls: list[dict[str, object]] = []

    class FakeItem:
        id = "Q123"

        def write(self, **kwargs: object) -> object:
            calls.append(kwargs)
            raise MWApiError({"code": "editconflict", "info": "editconflict"})

    monkeypatch.setattr(uploader, "_build_wbi_item", lambda item: (FakeItem(), 1, ["P3959"]))
    monkeypatch.setattr("converter.wikidata.uploader.time.sleep", lambda seconds: None)
    uploader.register_foreign_accept("Q123")
    result = uploader.upload_item(manuscript())
    assert result.status == "failed"
    assert "check foreign approval" in result.message.lower()
    assert len(calls) == 1
    assert not uploader._foreign_accept_qids


def test_foreign_approval_does_not_retry_a_pre_write_error(
    uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def build(item: WikidataItem) -> object:
        calls.append("build")
        raise RuntimeError("temporary read failure")

    monkeypatch.setattr(uploader, "_build_wbi_item", build)
    monkeypatch.setattr("converter.wikidata.uploader.time.sleep", lambda seconds: None)
    uploader.register_foreign_accept("Q123")
    result = uploader.upload_item(manuscript())
    assert result.status == "failed"
    assert calls == ["build"]
    assert not uploader._foreign_accept_qids


def test_test_stub_timeout_does_not_fall_back_to_another_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    up = WikidataUploader(token="Fixture@Bot:not-a-credential", is_test=True)
    calls: list[str] = []

    def post(*args: object, **kwargs: object) -> object:
        calls.append("post")
        raise requests.exceptions.ReadTimeout("response timed out")

    monkeypatch.setattr(
        up, "_login", SimpleNamespace(get_session=lambda: SimpleNamespace(post=post)),
    )
    monkeypatch.setattr(up, "_get_csrf_token", lambda: "fixture")
    monkeypatch.setattr(up, "_rate_limit", lambda: None)
    monkeypatch.setattr(
        up, "_adopt_by_exact_label_from_new_data", lambda **kwargs: calls.append("search"),
    )
    with pytest.raises(RuntimeError, match="outcome unknown"):
        up._wbeditentity_new(new="property", data={})
    assert calls == ["post"]
