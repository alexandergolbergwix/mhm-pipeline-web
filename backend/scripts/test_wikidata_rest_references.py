"""Test one dedicated reference repair on test.wikidata.org only."""

import json
import os
import re
import uuid
from copy import deepcopy

import httpx

ORIGIN = "https://test.wikidata.org"
ITEMS = f"{ORIGIN}/w/rest.php/wikibase/v1/entities/items"
ACTION = f"{ORIGIN}/w/api.php"
ACTOR = "Alexander Goldberg IL"
ACTOR_ID = 9241
USER_AGENT = "MHMPipeline/1.0 (https://mhm-pipeline.org; test wiki reference canary)"
URL = "https://mhm-pipeline.org/"
STATEMENT_PATH = "/statements/P31/0"


def part(pid, value):
    return {"property": {"id": pid}, "value": {"type": "value", "content": value}}


def reference(day):
    return {"parts": [
        part("P31", URL),
        part("P388", {
            "time": f"+2026-11-{day:02}T00:00:00Z",
            "precision": 11,
            "calendarmodel": "http://www.wikidata.org/entity/Q1985727",
        }),
    ]}


def references():
    return [
        reference(17), reference(18),
        {"parts": [part("P31", "https://www.mediawiki.org/")]},
    ]


def fixture():
    return {
        "labels": {"en": f"MHM reference REST test {uuid.uuid4().hex[:12]}"},
        "descriptions": {"en": "test item for an exact reference repair"},
        "statements": {"P31": [{
            **part("P31", URL),
            "rank": "preferred",
            "qualifiers": [part("P31", "https://test.wikidata.org/")],
            "references": references(),
        }]},
    }


def require(response, expected, stage):
    if response.status_code != expected:
        raise ValueError(f"{stage}: expected HTTP {expected}; received {response.status_code}")


def revision(response):
    tag = response.headers.get("ETag", "")
    match = re.fullmatch(r'(?:W/)?"([1-9][0-9]*)"', tag)
    if not match:
        raise ValueError("The test wiki did not return a revision ETag.")
    return match[1]


def action(client, **params):
    response = client.get(ACTION, params={**params, "format": "json", "formatversion": "2"})
    require(response, 200, "Action API")
    payload = response.json()
    if "error" in payload or not isinstance(payload.get("query"), dict):
        raise ValueError("The test wiki did not confirm the Action API check.")
    return payload["query"]


def assert_actor(client):
    user = action(client, action="query", meta="userinfo", uiprop="rights")["userinfo"]
    if user.get("id") != ACTOR_ID or user.get("name") != ACTOR or "edit" not in user["rights"]:
        raise ValueError("The credential does not identify the expected test wiki editor.")


def assert_creator(client, qid):
    pages = action(
        client, action="query", titles=qid, prop="revisions", rvdir="newer",
        rvlimit="1", rvprop="ids|user|userid",
    )["pages"]
    if len(pages) != 1 or pages[0].get("title") != qid or "redirect" in pages[0]:
        raise ValueError("The test wiki did not confirm the exact fixture item.")
    creator = pages[0]["revisions"][0]
    if creator.get("userid") != ACTOR_ID or creator.get("user") != ACTOR:
        raise ValueError("The test wiki creator does not match the authenticated editor.")


def patch_for(item):
    statement, = item["statements"]["P31"]
    refs = statement["references"]
    actual = [[(p["property"]["id"], p["value"]["content"]) for p in r["parts"]] for r in refs]
    expected = [
        [(p["property"]["id"], p["value"]["content"]) for p in r["parts"]]
        for r in references()
    ]
    if actual != expected or statement["rank"] != "preferred" or not statement["qualifiers"]:
        raise ValueError("The test fixture does not contain the exact expected evidence.")
    return [
        {"op": "test", "path": f"{STATEMENT_PATH}/id", "value": statement["id"]},
        {"op": "test", "path": f"{STATEMENT_PATH}/references/0", "value": refs[0]},
        {"op": "remove", "path": f"{STATEMENT_PATH}/references/0"},
    ]


def run(client):
    assert_actor(client)
    created = client.post(
        ITEMS, json={"item": fixture(), "comment": "Create MHM REST test fixture"}
    )
    require(created, 201, "Create test item")
    qid = created.json().get("id", "")
    if not re.fullmatch(r"Q[1-9][0-9]*", qid):
        raise ValueError("The test wiki did not confirm the fixture QID.")
    print(json.dumps({"test_item": f"{ORIGIN}/wiki/{qid}"}), flush=True)
    url = f"{ITEMS}/{qid}"
    assert_creator(client, qid)
    read = client.get(url)
    require(read, 200, "Read test item")
    before, before_rev = read.json(), revision(read)
    patch = patch_for(before)
    expected = deepcopy(before)
    expected["statements"]["P31"][0]["references"].pop(0)

    def send(rev, ops):
        return client.patch(
            url,
            headers={
                "If-Match": f'"{rev}"',
                "Content-Type": "application/json-patch+json",
            },
            json={"patch": ops, "comment": "Check exact MHM test reference repair"},
        )

    edited = send(before_rev, patch)
    require(edited, 200, "Remove older reference")
    after = client.get(url)
    require(after, 200, "Verify test item")
    after_rev = revision(after)
    if (
        int(after_rev) <= int(before_rev)
        or revision(edited) != after_rev
        or after.json() != expected
    ):
        raise ValueError("The public test item does not match the exact expected result.")

    stale = send(before_rev, patch)
    require(stale, 412, "Reject stale revision")
    final = client.get(url)
    require(final, 200, "Verify stale request made no change")
    if revision(final) != after_rev or final.json() != expected:
        raise ValueError("The stale request changed the test item.")
    print(json.dumps({"before_revision": before_rev, "after_revision": after_rev,
                      "stale_status": 412, "full_item_match": True}))


def main():
    token = os.getenv("WIKIDATA_REST_TEST_TOKEN", "").strip()
    if not token:
        raise SystemExit("Set WIKIDATA_REST_TEST_TOKEN with a test wiki OAuth bearer token.")
    try:
        with httpx.Client(
            headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}",
                     "Cache-Control": "no-cache"},
            timeout=15, follow_redirects=False,
        ) as client:
            run(client)
    except (ValueError, httpx.HTTPError) as exc:
        raise SystemExit(f"Test stopped: {exc}") from None


if __name__ == "__main__":
    main()
