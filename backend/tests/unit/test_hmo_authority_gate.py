from types import SimpleNamespace
import uuid

from app.pipeline.hmo_authority_conflict_resolve import plan_conflict_unapprovals
from app.pipeline.hmo_authority_gate import (
    build_authority_conflict_report,
    format_authority_gate_error,
    validate_authority_rows,
)


def test_two_source_place_is_ready():
    result = validate_authority_rows([
        SimpleNamespace(
            approved=True,
            entity_text="Jerusalem",
            wikidata_qid="Q1218",
            mazal_id="987007270341205171",
            viaf_id="",
        )
    ])
    assert result["ready"]


def test_conflicting_approved_qid_blocks():
    result = validate_authority_rows([
        SimpleNamespace(approved=True, entity_text="A", wikidata_qid="Q1", mazal_id="", viaf_id=""),
        SimpleNamespace(approved=True, entity_text="B", wikidata_qid="Q1", mazal_id="", viaf_id=""),
    ])
    assert not result["ready"]
    assert result["conflicts"]
    msg = format_authority_gate_error(result)
    assert "conflict wikidata=Q1" in msg
    assert "A" in msg and "B" in msg
    assert "Authority conflicts panel" in msg


def test_nli_id_in_viaf_field_blocks():
    result = validate_authority_rows([
        SimpleNamespace(approved=True, entity_text="A", wikidata_qid="", mazal_id="", viaf_id="987007123")
    ])
    assert not result["ready"]
    assert result["invalid"]
    msg = format_authority_gate_error(result)
    assert "invalid viaf=987007123" in msg


def test_rich_report_includes_match_ids():
    a = uuid.uuid4()
    b = uuid.uuid4()
    report = build_authority_conflict_report([
        SimpleNamespace(
            id=a, approved=True, entity_text="Person A", matched_name="A",
            control_number="1", entity_kind="person", role="author",
            confidence="high", source="mazal",
            wikidata_qid="Q1", mazal_id="", viaf_id="",
        ),
        SimpleNamespace(
            id=b, approved=True, entity_text="Person B", matched_name="B",
            control_number="2", entity_kind="person", role="author",
            confidence="medium", source="mazal",
            wikidata_qid="Q1", mazal_id="", viaf_id="",
        ),
    ])
    assert not report["ready"]
    assert report["conflict_count"] == 1
    owners = report["conflicts"][0]["owners"]
    assert {o["match_id"] for o in owners} == {str(a), str(b)}


def test_plan_keep_one_unapproves_siblings():
    a = uuid.uuid4()
    b = uuid.uuid4()
    c = uuid.uuid4()
    rows = [
        SimpleNamespace(
            id=a, approved=True, entity_text="A", wikidata_qid="Q1",
            mazal_id="", viaf_id="", control_number="1", role="author",
            matched_name="", entity_kind="person", confidence="high", source="t",
        ),
        SimpleNamespace(
            id=b, approved=True, entity_text="B", wikidata_qid="Q1",
            mazal_id="", viaf_id="", control_number="2", role="author",
            matched_name="", entity_kind="person", confidence="high", source="t",
        ),
        SimpleNamespace(
            id=c, approved=True, entity_text="C", wikidata_qid="Q9",
            mazal_id="", viaf_id="", control_number="3", role="author",
            matched_name="", entity_kind="person", confidence="high", source="t",
        ),
    ]
    targets = plan_conflict_unapprovals(
        rows, keep_match_ids=[a], unapprove_match_ids=[],
    )
    assert {t.id for t in targets} == {b}
