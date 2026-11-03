"""Rule W-280 — reconciliation reads are batched before the per-item loop."""

from __future__ import annotations

from types import SimpleNamespace

from converter.wikidata.reconciler import (
    WikidataReconciler,
    prewarm_identifier_cache,
    prewarm_reconciler_for_items,
)


class FakeReconciler(WikidataReconciler):
    def __init__(self) -> None:
        super().__init__()
        self.queries: list[str] = []

    def _query(self, sparql: str):  # noqa: ANN201
        self.queries.append(sparql)
        if "P3959" in sparql:
            return [
                {"id": {"value": "9901"}, "item": {"value": "http://www.wikidata.org/entity/Q1"}},
            ]
        return []


def test_prewarm_batches_identifiers_into_one_query() -> None:
    r = FakeReconciler()
    prewarm_identifier_cache(
        r,
        [("ms:nnl", "P3959", "9901"), ("ms:nnl", "P3959", "9902")],
    )
    assert len(r.queries) == 1
    assert r._cache["ms:nnl:9901"] == "Q1"
    assert r._cache["ms:nnl:9902"] is None
    assert r.reconcile_manuscript_by_nnl_id("9901") == "Q1"
    assert len(r.queries) == 1


def test_prewarm_for_items_collects_probes_by_type() -> None:
    r = FakeReconciler()
    items = [
        SimpleNamespace(
            entity_type="manuscript",
            statements=[
                SimpleNamespace(property_id="P3959", value="9901"),
                SimpleNamespace(property_id="P217", value="Ms. Heb. 1"),
            ],
        ),
        SimpleNamespace(
            entity_type="person",
            statements=[SimpleNamespace(property_id="P214", value="123")],
        ),
    ]
    prewarm_reconciler_for_items(r, items)
    joined = " | ".join(r.queries)
    assert "P3959" in joined
    assert "P217" in joined
    assert "P214" in joined
    assert r._cache["person:viaf:123"] is None


def test_prewarm_exact_match_uris_batches_p2888_for_works() -> None:
    class P2888Reconciler(WikidataReconciler):
        def __init__(self) -> None:
            super().__init__()
            self.queries: list[str] = []

        def _query(self, sparql: str):  # noqa: ANN201
            self.queries.append(sparql)
            if "P2888" in sparql:
                return [
                    {
                        "uri": {"value": "https://mhm-hmo.wikibase.cloud/wiki/Item:Q22084"},
                        "item": {"value": "http://www.wikidata.org/entity/Q141755060"},
                    },
                ]
            return []

    r = P2888Reconciler()
    items = [
        SimpleNamespace(
            entity_type="work",
            labels={"he": "כנף רננים"},
            statements=[
                SimpleNamespace(
                    property_id="P2888",
                    value="https://mhm-hmo.wikibase.cloud/wiki/Item:Q22084",
                ),
            ],
        ),
    ]
    prewarm_reconciler_for_items(r, items)
    assert r.reconcile_by_exact_match_uri(
        "https://mhm-hmo.wikibase.cloud/wiki/Item:Q22084"
    ) == "Q141755060"
    assert sum(1 for q in r.queries if "P2888" in q) == 1

