"""The download route keeps blocked diagnostics out of the TSV command stream."""

from types import SimpleNamespace

import pytest

from app.routers import wikidata_studio as router
from converter.wikidata.item_models import WikidataItem, WikidataStatement


@pytest.mark.asyncio
async def test_gated_download_sanitizes_headers_and_exports_adopted_qid(sample_run, monkeypatch):
    item = WikidataItem(
        local_id="ms:1", entity_type="manuscript", labels={"en": "Manuscript"},
        statements=[WikidataStatement("P31", "Q5", "item")],
    )

    async def load(*args, **kwargs):
        return [item]

    async def ledger(*args, **kwargs):
        return {}

    def prepare(items, *, ledger):
        items[0].existing_qid = "Q42"
        blocked = SimpleNamespace(
            local_id="bad\r\nQ9\tP31\tQ5", method="blocked",
            block_message="reason\nQ8\tP31\tQ5",
        )
        return [SimpleNamespace(item=items[0])], [blocked]

    monkeypatch.setattr(router, "_build_native_items", load)
    monkeypatch.setattr(router.wikidata_upload, "load_ledger_for_prepare", ledger)
    monkeypatch.setattr(router.wikidata_upload, "prepare_items_for_export", prepare)
    response = await sample_run["client"].get(
        f"/api/runs/{sample_run['run_id']}/wikidata-studio/quickstatements.txt?source=legacy",
    )
    assert response.status_code == 200
    assert "\nQ9\t" not in response.text and "\nQ8\t" not in response.text
    assert "Q42\tP31\tQ5" in response.text
    assert "CREATE" not in response.text
