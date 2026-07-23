from types import SimpleNamespace
from app.pipeline.hmo_authority_gate import validate_authority_rows

def test_two_source_place_is_ready():
    result = validate_authority_rows([SimpleNamespace(approved=True, entity_text="Jerusalem", wikidata_qid="Q1218", mazal_id="987007270341205171", viaf_id="")])
    assert result["ready"]

def test_conflicting_approved_qid_blocks():
    result = validate_authority_rows([SimpleNamespace(approved=True, entity_text="A", wikidata_qid="Q1", mazal_id="", viaf_id=""), SimpleNamespace(approved=True, entity_text="B", wikidata_qid="Q1", mazal_id="", viaf_id="")])
    assert not result["ready"]
    assert result["conflicts"]

def test_nli_id_in_viaf_field_blocks():
    result = validate_authority_rows([SimpleNamespace(approved=True, entity_text="A", wikidata_qid="", mazal_id="", viaf_id="987007123")])
    assert not result["ready"]
    assert result["invalid"]
