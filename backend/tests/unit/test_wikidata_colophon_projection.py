"""Source-exact colophon wrappers cannot return after a canonical rebuild."""

from copy import deepcopy

import pytest

from app.pipeline.hmo_canonical_wikidata import (
    CanonicalStudioContext,
    _sanitize_canonical_claims,
    build_canonical_studio_result,
)
from converter.wikidata.item_models import WikidataItem, WikidataStatement

CN = "990000000000005171"
TEXT = 'אני "שמואל(?)" השלמתי'


def _colophon(text: str, *, scoped: bool, url: str) -> WikidataStatement:
    source = [
        {"property": "P3959", "type": "external-id", "value": CN},
        {"property": "P854", "type": "url", "value": url},
    ]
    return WikidataStatement(
        "P1684", text, "monolingualtext", language="he",
        qualifiers=[
            {"property": "P3831", "type": "item", "value": "Q372474"},
            {"property": "P958", "type": "string", "value": "1r"},
        ] if scoped else [],
        references=deepcopy(source), reference_blocks=[deepcopy(source)],
    )


def _context() -> CanonicalStudioContext:
    return CanonicalStudioContext(marc_by_cn={CN: {"colophon_text": TEXT}})


@pytest.mark.parametrize("wrapper", ['{}', '"{}"', '"{}".'])
def test_source_exact_wrapper_references_survive_on_the_scoped_colophon(wrapper: str) -> None:
    old = _colophon(wrapper.format(TEXT.replace('"', '""')), scoped=False, url="https://example.org/old")
    retained = _colophon(TEXT, scoped=True, url="https://example.org/scoped")
    context = _context()
    item = WikidataItem(entity_type="manuscript", records=[CN], statements=[old, retained])
    old_source = deepcopy(old.reference_blocks)
    retained_source = deepcopy(retained.reference_blocks)

    _sanitize_canonical_claims([item], context)

    assert len(item.statements) == 1
    assert item.statements[0].value == TEXT
    assert item.statements[0].reference_blocks == retained_source + old_source
    assert item.statements[0].qualifiers == retained.qualifiers


@pytest.mark.parametrize(
    "difference", ["uncertainty", "language", "source", "rank", "scope", "ambiguous"],
)
def test_colophon_cleanup_preserves_different_or_ambiguous_evidence(difference: str) -> None:
    old = _colophon(f'"{TEXT}".', scoped=False, url="https://example.org/old")
    retained = _colophon(TEXT, scoped=True, url="https://example.org/scoped")
    statements = [old, retained]
    if difference == "uncertainty":
        old.value = str(old.value).replace("(?)", "")
    elif difference == "language":
        old.language = "en"
    elif difference == "source":
        old.references[0]["value"] = "990000000000015171"
        old.reference_blocks[0][0]["value"] = "990000000000015171"
    elif difference == "rank":
        old.rank = "preferred"
    elif difference == "scope":
        old.qualifiers = [{"property": "P958", "type": "string", "value": "2r"}]
    else:
        second = deepcopy(retained)
        second.qualifiers[1]["value"] = "2r"
        statements.append(second)
    item = WikidataItem(entity_type="manuscript", records=[CN], statements=statements)
    before = deepcopy(item.statements)

    _sanitize_canonical_claims([item], _context())

    assert item.statements == before


def test_colophon_cleanup_runs_after_positional_curator_edits() -> None:
    old = _colophon(f'"{TEXT}".', scoped=False, url="https://example.org/old")
    retained = _colophon(TEXT, scoped=True, url="https://example.org/scoped")
    item = WikidataItem(
        local_id="ms:1", entity_type="manuscript", labels={"en": "Example manuscript"},
        records=[CN], statements=[
            old, WikidataStatement("P407", "Q1860", "item"), retained,
            WikidataStatement("P31", "Q87167", "item"),
            WikidataStatement("P3959", CN, "external-id"),
        ],
    )
    result = build_canonical_studio_result(
        [], context=_context(), legacy_native_items=[item], return_native=True,
        overrides={"ms:1": {"statement_edits": {"1": {"value": "Q9288"}}}},
    )
    rebuilt = next(i for i in result["native_items"] if i.local_id == "ms:1")
    assert [s.value for s in rebuilt.statements if s.property_id == "P407"] == ["Q9288"]
    assert len([s for s in rebuilt.statements if s.property_id == "P1684"]) == 1
