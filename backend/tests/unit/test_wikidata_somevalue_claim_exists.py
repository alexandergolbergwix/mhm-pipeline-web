from types import SimpleNamespace

from converter.wikidata.item_models import WikidataStatement
from converter.wikidata.uploader import WikidataUploader


def _scribe(name: str) -> WikidataStatement:
    return WikidataStatement(
        property_id="P11603", value=None, value_type="somevalue",
        qualifiers=[{"property": "P1932", "value": name, "type": "string"}],
    )


def _uploader() -> WikidataUploader:
    return WikidataUploader(token="Fixture@Bot:not-a-credential", is_test=False, allow_live=True)


def _wiki_item(uploader: WikidataUploader, *statements: WikidataStatement) -> SimpleNamespace:
    claims = [uploader._build_claim(stmt) for stmt in statements]
    return SimpleNamespace(claims=SimpleNamespace(get=lambda pid: [c for c in claims if c.mainsnak.property_number == pid]))


def test_existing_somevalue_claim_with_the_same_name_is_recognized() -> None:
    uploader = _uploader()
    wiki = _wiki_item(uploader, _scribe("אהרן בן יצחק"))
    assert uploader._claim_exists(wiki, _scribe("אהרן בן יצחק")) is True


def test_somevalue_claim_with_a_different_name_is_missing() -> None:
    uploader = _uploader()
    wiki = _wiki_item(uploader, _scribe("אהרן בן יצחק"))
    assert uploader._claim_exists(wiki, _scribe("משה בן יעקב")) is False
