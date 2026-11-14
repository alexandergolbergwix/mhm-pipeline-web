"""Foreign approvals and cached classifications cannot grant creator ownership."""

import pytest

from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.quickstatements import QuickStatementsExporter
from converter.wikidata.uploader import UnauthorisedModificationError, WikidataUploader


@pytest.mark.parametrize("is_test", [False, True])
@pytest.mark.parametrize("creator", ["Other", None])
@pytest.mark.parametrize("stage", ["_build_wbi_item", "pre_write", "exact_repair_pre_write"])
def test_a_legacy_approval_and_cached_ownership_cannot_authorize_a_foreign_edit(
    monkeypatch: pytest.MonkeyPatch, is_test: bool, creator: str | None, stage: str,
) -> None:
    uploader = WikidataUploader(token="", is_test=is_test)
    monkeypatch.setattr(uploader, "_get_authenticated_user", lambda: "Project")
    monkeypatch.setattr(uploader, "_get_first_revision_author", lambda qid: creator)
    uploader._foreign_accept_qids.add("Q123")
    uploader._is_our_item_cache["Q123"] = True
    with pytest.raises(UnauthorisedModificationError):
        uploader._assert_modifiable("Q123", stage, our_p3959="990001")


def test_an_unknown_account_cannot_use_a_cached_owned_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uploader = WikidataUploader(token="")
    monkeypatch.setattr(uploader, "_get_authenticated_user", lambda: None)
    monkeypatch.setattr(uploader, "_get_first_revision_author", lambda qid: "Project")
    uploader._is_our_item_cache["Q123"] = True
    with pytest.raises(UnauthorisedModificationError):
        uploader._assert_modifiable("Q123", "pre_write")


def test_quickstatements_can_reference_an_existing_item_without_edit_commands() -> None:
    existing = WikidataItem(
        local_id="work:1", entity_type="work", existing_qid="Q123",
        labels={"en": "Existing work"}, statements=[WikidataStatement("P31", "Q47461344", "item")],
    )
    new = WikidataItem(
        local_id="ms:1", entity_type="manuscript", labels={"en": "New manuscript"},
        statements=[WikidataStatement("P1574", "Q123", "item")],
    )
    output = QuickStatementsExporter().export([existing, new])
    commands = [line for line in output.splitlines() if not line.startswith("/*")]
    assert "LAST\tP1574\tQ123" in commands
    assert not any(line.startswith("Q123\t") for line in commands)
    assert "existing work is not exported" in output
