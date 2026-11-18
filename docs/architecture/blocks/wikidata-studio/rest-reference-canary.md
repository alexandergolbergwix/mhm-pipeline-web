# REST reference test on test Wikidata

`backend/scripts/test_wikidata_rest_references.py` runs one test on a new fixture.
The tool accepts only a test wiki OAuth bearer token in `WIKIDATA_REST_TEST_TOKEN`.
Supply the token through a process environment, and keep it out of command arguments.
The tool checks the actor and creator on test.wikidata.org.
It uses no production endpoint.

The fixture has two versions of a source with different retrieval dates.
It also has a distinct reference, a qualifier, and a preferred rank.
The tool takes the current revision from the REST ETag.
The patch tests the statement ID and the exact old reference before removal.
The tool checks the full item after the edit.
It sends the same patch with the old revision and requires HTTP 412.
It checks that this request did not change the item.
The tool leaves the fixture on test Wikidata so an operator can check its history.

```sh
cd backend
.venv/bin/python -m scripts.test_wikidata_rest_references
```

The tests in `test_wikidata_rest_reference_canary.py` use a simulated REST service.
A successful canary does not permit the production queue to bypass a lag delay.

## Local result on November 18, 2026

A disposable Wikibase with MediaWiki 1.46 accepted the item patch with HTTP 200.
Fixture `Q6` went from revision 16 to revision 17.
The patch removed the older catalogue reference and kept the other two.
The full item matched the expected result, including the qualifier and preferred rank.
A repeat patch with revision 16 returned HTTP 412 and left revision 17 unchanged.
This run used a local session.
It did not test Wikimedia OAuth, live Wikidata, or the production queue.
