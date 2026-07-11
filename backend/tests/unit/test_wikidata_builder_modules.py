"""Public compatibility checks for the modular Wikidata item builder."""

from __future__ import annotations

from converter.wikidata.item_builder import WikidataItem, WikidataItemBuilder, WikidataStatement
from converter.wikidata.item_models import WikidataItem as ModelItem
from converter.wikidata.item_models import WikidataStatement as ModelStatement


def test_builder_reexports_shared_item_models() -> None:
    assert WikidataItem is ModelItem
    assert WikidataStatement is ModelStatement


def test_builder_keeps_projection_methods_on_the_public_api() -> None:
    builder = WikidataItemBuilder()

    assert callable(builder.build_manuscript_item)
    assert callable(builder._add_person_claims)
    assert callable(builder._get_or_create_person)
    assert callable(builder._get_or_create_work)
