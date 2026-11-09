"""Fresh builder references retain their source boundaries."""

from copy import deepcopy

import pytest

from app.pipeline.wikidata_canonical_enrichment import dedupe_statements
from app.pipeline.wikidata_studio import (
    _build_native_sync,
    build_items_for_run,
    native_item_from_payload,
    native_item_payload,
    validate_upload_reference_blocks,
)
from app.publication.digests import canonical_digest, freeze_json, thaw_json
from app.publication.types import PublicationEntity
from app.publication.wikidata_gateway import CurrentWikidataBoundary, _SafeWikidataGatewaySession
from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.property_mapping import (
    extend_source_reference,
    nli_authority_reference,
    nli_reference,
    viaf_reference,
)
from tests.unit.test_wikidata_studio_works import _fake_marc_record


@pytest.mark.parametrize("source", [
    nli_reference("990000000000000001"),
    nli_authority_reference("987000000000000001"),
    viaf_reference("12345"),
])
def test_fresh_sources_and_safe_copies_are_one_block(source):
    copied = deepcopy(source)
    assert WikidataStatement("P31", "Q5", "item", references=source.copy()).reference_blocks
    extended = extend_source_reference(
        copied, [{"property": "P887", "value": "Q42", "type": "item"}],
    )
    statement = WikidataStatement("P31", "Q5", "item", references=extended)
    assert statement.reference_blocks == [list(extended)]
    assert statement.references == list(extended)
    source[0]["value"] = "Q999"
    with pytest.raises(ValueError, match="modified"):
        WikidataStatement("P31", "Q5", "item", references=source)
    assert statement.reference_blocks[0][0]["value"] != "Q999"


@pytest.mark.asyncio
async def test_full_builder_merge_studio_and_publication_round_trip():
    first_cn = "990000000000000001"
    second_cn = "990000000000000002"
    first_result = await build_items_for_run(
        marc_records=[_fake_marc_record(first_cn)], approved_matches=[], return_native=True,
    )
    second_result = await build_items_for_run(
        marc_records=[_fake_marc_record(second_cn)], approved_matches=[], return_native=True,
    )
    first = next(item for item in first_result["native_items"] if item.entity_type == "manuscript")
    second = next(
        item for item in second_result["native_items"] if item.entity_type == "manuscript"
    )
    first_claim = next(s for s in first.statements if s.property_id == "P31" and s.references)
    second_claim = next(
        s for s in second.statements if s.property_id == "P31" and s.value == first_claim.value
    )
    assert len(first_claim.reference_blocks) == 1
    assert any(s.get("property") == "P3959" and s.get("value") == first_cn
               for s in first_claim.reference_blocks[0])
    studio = next(item for item in first_result["items"] if item["entity_type"] == "manuscript")
    assert next(s for s in studio["statements"] if s["property_id"] == "P31")["reference_blocks"]
    merged = dedupe_statements([first_claim, second_claim])[0]
    assert len(merged.reference_blocks) == 2
    assert {s["value"] for block in merged.reference_blocks for s in block
            if s.get("property") == "P3959"} == {first_cn, second_cn}
    first.statements = [merged]
    validate_upload_reference_blocks([first])
    payload = native_item_payload(first)
    restored = native_item_from_payload(payload)
    assert restored.statements[0].reference_blocks == merged.reference_blocks
    entity = PublicationEntity(
        release_id="release", entity_key="ms:1", entity_type="manuscript",
        entity_digest=canonical_digest(payload), document=freeze_json(payload),
        evidence_refs=(), identity_assertions=(), local_references=(),
    )
    mutation = await _SafeWikidataGatewaySession(None).compile_mutation(
        entity, action="create", target_qid=None,
    )
    assert (
        thaw_json(mutation.document)["statements"][0]["reference_blocks"]
        == merged.reference_blocks
    )
    assert (
        CurrentWikidataBoundary._native_item(entity).statements[0].reference_blocks
        == merged.reference_blocks
    )


def test_full_builder_sources_people_work_and_colophon():
    record = {
        **_fake_marc_record("990000000000000001"),
        "title": "ספר הדרושים",
        "authors": [{"name": "Moses Gaster", "role": "author"}],
        "marc_authority_matches": [{
            "name": "Moses Gaster", "role": "author", "approved": True,
            "entity_kind": "person", "viaf_uri": "https://viaf.org/viaf/12345",
        }],
        "data_from_colophon": ["dates"],
    }
    items = _build_native_sync([record])
    assert {item.entity_type for item in items} >= {"manuscript", "person", "work"}
    for item in items:
        for statement in item.statements:
            if statement.references:
                assert statement.reference_blocks, (item.entity_type, statement.property_id)
    manuscript = next(item for item in items if item.entity_type == "manuscript")
    inception = next(s for s in manuscript.statements if s.property_id == "P571")
    assert len(inception.reference_blocks) == 1
    assert any(s["property"] == "P887" for s in inception.reference_blocks[0])
    person = next(item for item in items if item.entity_type == "person")
    assert any(s["value"] == "https://viaf.org/viaf/12345"
               for claim in person.statements for block in claim.reference_blocks for s in block)
    validate_upload_reference_blocks(items)


def test_transcription_merge_keeps_distinct_sources_on_distinct_texts():
    from app.pipeline.hmo_canonical_wikidata import _merge_transcription_statements

    first = WikidataStatement("P1684", '"first"', "string", references=nli_reference("1"))
    second = WikidataStatement("P1684", "second\nline", "string", references=nli_reference("2"))
    claims = [first, second]
    _merge_transcription_statements(claims, control_number="1")
    assert [claim.value for claim in claims] == ['"first"', "second\nline"]
    assert [claim.reference_blocks for claim in claims] == [
        [list(nli_reference("1"))], [list(nli_reference("2"))],
    ]


def test_transcription_equal_complete_texts_can_merge_sources():
    from app.pipeline.hmo_canonical_wikidata import _merge_transcription_statements

    claims = [
        WikidataStatement("P1684", text, "string", references=nli_reference(source))
        for text, source in [("alpha", "1"), ("beta", "1"), ("alpha", "2"), ("beta", "2")]
    ]
    _merge_transcription_statements(claims, control_number="1")
    assert len(claims) == 1
    assert claims[0].value == "alpha | beta"
    assert {snak["value"] for block in claims[0].reference_blocks for snak in block
            if snak["property"] == "P3959"} == {"1", "2"}


def test_transcription_source_identity_ignores_snak_order():
    from app.pipeline.hmo_canonical_wikidata import _merge_transcription_statements

    source = list(nli_reference("1"))
    claims = [
        WikidataStatement("P1684", "alpha", "string", references=source,
                          reference_blocks=[source]),
        WikidataStatement("P1684", "beta", "string", references=list(reversed(source)),
                          reference_blocks=[list(reversed(source))]),
    ]
    _merge_transcription_statements(claims, control_number="1")
    assert len(claims) == 1
    assert claims[0].value == "alpha | beta"
    assert claims[0].reference_blocks == [source]


def test_transcription_does_not_join_different_qualifiers_or_ranks():
    from app.pipeline.hmo_canonical_wikidata import _merge_transcription_statements

    source = nli_reference("1")
    claims = [
        WikidataStatement("P1684", "alpha", "string", references=source),
        WikidataStatement("P1684", "beta", "string", references=source,
                          qualifiers=[{"property": "P958", "value": "1r", "type": "string"}]),
        WikidataStatement("P1684", "gamma", "string", references=source, rank="preferred"),
    ]
    _merge_transcription_statements(claims, control_number="1")
    assert [claim.value for claim in claims] == ["alpha", "beta", "gamma"]
    assert claims[1].qualifiers[0]["value"] == "1r"
    assert claims[2].rank == "preferred"


def test_transcription_does_not_join_ambiguous_flat_sources():
    from app.pipeline.hmo_canonical_wikidata import _merge_transcription_statements

    source = list(nli_reference("1"))
    claims = [WikidataStatement("P1684", text, "string", references=deepcopy(source))
              for text in ("alpha", "beta")]
    _merge_transcription_statements(claims, control_number="1")
    assert [claim.value for claim in claims] == ["alpha", "beta"]
    assert all(claim.references == source and not claim.reference_blocks for claim in claims)
    with pytest.raises(ValueError, match="Ambiguous flat references"):
        validate_upload_reference_blocks([WikidataItem(statements=claims)])


def test_full_builder_heuristic_reference_is_one_source(monkeypatch):
    import converter.wikidata.content_projection as content
    import converter.wikidata.marc_subject_resolve as genres

    class Classifier:
        def predict(self, title, notes):
            return [("inferred", 0.9)]

    monkeypatch.setattr(content, "_get_genre_classifier", lambda: Classifier())
    monkeypatch.setattr(genres, "resolve_genre_qid", lambda *args, **kwargs: "Q42")
    record = {**_fake_marc_record("990000000000000001"), "allow_inferred_genre": True}
    manuscript = next(item for item in _build_native_sync([record])
                      if item.entity_type == "manuscript")
    genre = next(s for s in manuscript.statements if s.property_id == "P136")
    assert len(genre.reference_blocks) == 1
    assert any(s["property"] == "P887" for s in genre.reference_blocks[0])
    validate_upload_reference_blocks([manuscript])


def test_canonical_subtitle_keeps_source_blocks_without_aliasing():
    from app.pipeline.hmo_canonical_wikidata import _split_work_title_claims

    source = nli_reference("1")
    title = WikidataStatement(
        "P1476", "Main title : Subtitle", "monolingualtext", references=source,
    )
    claims = [title]
    _split_work_title_claims(claims)
    assert len(claims) == 2
    assert claims[1].reference_blocks == title.reference_blocks == [list(source)]
    claims[1].reference_blocks[0][0]["value"] = "Q999"
    assert title.reference_blocks[0][0]["value"] != "Q999"


def test_ambiguous_flat_import_cannot_be_laundered_by_merge():
    source = nli_reference("990000000000000001")
    old = WikidataStatement("P31", "Q5", "item", references=list(source))
    fresh = WikidataStatement("P31", "Q5", "item", references=source)
    with pytest.raises(ValueError, match="Ambiguous flat references"):
        dedupe_statements([old, fresh])
    assert old.reference_blocks == []
    assert len(fresh.reference_blocks) == 1
    copied = extend_source_reference(list(source), [{"property": "P887", "value": "Q42"}])
    assert not WikidataStatement("P31", "Q5", "item", references=copied).reference_blocks
    with pytest.raises(ValueError, match="not freshly constructed"):
        fresh.set_fresh_source(copied)
