from app.pipeline.hmo_canonical import normalize_live_entity
from app.pipeline.hmo_canonical_wikidata import native_wikidata_claims

def test_native_claim_mapping_requires_explicit_property_and_value() -> None:
    entity = normalize_live_entity({"local_id": "Manuscript_A", "source_uri": "https://w3id.org/mhm/ontology#Manuscript_A", "wikibase_id": "Q1252", "claims": [{"wikidata_property": "P50", "value_type": "wikibase-item", "target_qid": "Q42"}, {"property_uri": "https://w3id.org/mhm/ontology#viaf_id", "value": "123"}, {"wikidata_property": "P31", "value_type": "wikibase-item", "target_qid": "not-a-qid"}]})
    assert native_wikidata_claims(entity) == [{"property": "P50", "value": "Q42"}]


def test_quickstatements_uses_only_native_claims() -> None:
    from app.pipeline.hmo_canonical_wikidata import quickstatements_from_canonical
    entity = normalize_live_entity({"local_id": "Manuscript_A", "source_uri": "https://w3id.org/mhm/ontology#Manuscript_A", "wikibase_id": "Q1252", "labels": {"en": "A manuscript"}, "claims": [{"wikidata_property": "P50", "value_type": "wikibase-item", "target_qid": "Q42"}, {"property_uri": "https://w3id.org/mhm/ontology#viaf_id", "value": "123"}]})
    text = quickstatements_from_canonical([entity])
    assert 'LAST	P50	Q42' in text
    assert 'viaf_id' not in text
