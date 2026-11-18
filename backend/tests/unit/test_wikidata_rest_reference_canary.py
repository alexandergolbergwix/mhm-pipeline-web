import json
from copy import deepcopy

import httpx
import pytest

from scripts import test_wikidata_rest_references as canary


@pytest.mark.parametrize("creator_id", [canary.ACTOR_ID, 42])
def test_canary_checks_creator_and_keeps_the_revision_precondition(creator_id, capsys):
    item = {}
    edits = []
    current_revision = 20

    def route(request):
        nonlocal item, current_revision
        assert request.url.host == "test.wikidata.org"
        if str(request.url).startswith(canary.ACTION):
            if request.url.params.get("meta") == "userinfo":
                return httpx.Response(200, json={"query": {"userinfo": {
                    "id": canary.ACTOR_ID, "name": canary.ACTOR, "rights": ["edit"],
                }}})
            return httpx.Response(200, json={"query": {"pages": [{
                "title": "Q123", "revisions": [{"userid": creator_id, "user": canary.ACTOR}],
            }]}})
        if request.method == "POST":
            item = json.loads(request.content)["item"]
            item["id"] = "Q123"
            item["statements"]["P31"][0]["id"] = "Q123$test"
            return httpx.Response(201, json=item)
        if request.method == "PATCH":
            assert request.headers["Content-Type"] == "application/json-patch+json"
            before_refs = deepcopy(item["statements"]["P31"][0]["references"])
            ops = json.loads(request.content)["patch"]
            edits.append(request.headers["If-Match"])
            if request.headers["If-Match"] != f'"{current_revision}"':
                return httpx.Response(412)
            assert ops[0]["value"] == "Q123$test"
            assert ops[1]["value"] == before_refs[0]
            assert ops[2] == {"op": "remove", "path": "/statements/P31/0/references/0"}
            item["statements"]["P31"][0]["references"].pop(0)
            current_revision += 1
        return httpx.Response(200, json=item, headers={"ETag": f'W/"{current_revision}"'})

    with httpx.Client(transport=httpx.MockTransport(route)) as client:
        if creator_id != canary.ACTOR_ID:
            with pytest.raises(ValueError, match="creator does not match"):
                canary.run(client)
            assert edits == []
        else:
            canary.run(client)
            output = capsys.readouterr().out.splitlines()
            assert json.loads(output[-1])["full_item_match"] is True
            assert edits == ['"20"', '"20"']
            assert current_revision == 21
