from types import SimpleNamespace

import pytest

from converter.wikidata.uploader import WikidataUploader


@pytest.mark.parametrize(
    "uid,creator_uid,hidden,redirect,revid,expected",
    [
        (6935067, 6935067, False, False, 42, True),
        (6935067, 7, False, False, 42, False),
        (None, 6935067, False, False, 42, False),
        (True, True, False, False, 42, False),
        (6935067, 6935067, True, False, 42, False),
        (6935067, 6935067, False, True, 42, False),
        (6935067, 6935067, False, False, None, False),
    ],
)
def test_fresh_first_revision_id_proof(
    monkeypatch, uid, creator_uid, hidden, redirect, revid, expected
):
    up = WikidataUploader(token="", allow_live=True)
    monkeypatch.setattr(
        up, "_query_userinfo_rights", lambda: {"id": uid, "name": "Alexander Goldberg IL"}
    )
    first = {"user": "Alexander Goldberg IL", "userid": creator_uid, "revid": revid}
    if hidden:
        first["userhidden"] = ""
    data = {
        "query": {"pages": {"1": {"title": "Q123", "pageid": 1, "ns": 0, "revisions": [first]}}}
    }
    if redirect:
        data["query"]["redirects"] = [{"from": "Q123", "to": "Q124"}]
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=200, json=lambda: data)

    up._login = SimpleNamespace(get_session=lambda: SimpleNamespace(get=get))
    assert (
        up._creator_identity_matches("Q123", "Alexander Goldberg IL", required_user_id=6935067)
        is expected
    )
    if calls:
        assert calls[0]["allow_redirects"] is False
        assert "userid" in calls[0]["params"]["rvprop"]


def test_cached_name_cannot_supply_the_new_id_proof(monkeypatch):
    up = WikidataUploader(token="")
    up._creator_cache["Q123"] = "Alexander Goldberg IL"
    up._is_our_item_cache["Q123"] = True
    monkeypatch.setattr(up, "_get_authenticated_user", lambda: "Alexander Goldberg IL")
    monkeypatch.setattr(
        up, "_query_userinfo_rights", lambda: {"id": 6935067, "name": "Alexander Goldberg IL"}
    )
    assert not up._creator_identity_matches("Q123", "Alexander Goldberg IL")
