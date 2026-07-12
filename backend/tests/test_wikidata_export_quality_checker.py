from __future__ import annotations

import json
from pathlib import Path

from scripts.check_wikidata_export_quality import audit


def test_audit_reports_compact_failures_from_approved_section_export(tmp_path: Path) -> None:
    source = tmp_path / "approved.json"
    source.write_text(
        json.dumps(
            {
                "approved_only": True,
                "items": [
                    {
                        "local_id": "work:bad",
                        "entity_type": "work",
                        "labels": {"he": "תרגום רסג"},
                        "descriptions": {"en": "Work by רסג"},
                        "existing_qid": None,
                        "statements": [],
                        "work_candidate_evidence": [
                            {"raw_title": 'תרגום רס"ג', "source_field": "505"}
                        ],
                    },
                    {
                        "local_id": "ms:ok",
                        "entity_type": "manuscript",
                        "labels": {"he": "כתב יד"},
                        "descriptions": {"en": "Hebrew manuscript"},
                        "statements": [],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = audit(source)

    assert report["items"] == 2
    assert report["export_mode"] == "authority-approved-only"
    assert report["counts"]["work_missing_existing_qid"] == 1
    assert report["counts"]["work_missing_author_claim"] == 1
    assert report["counts"]["english_description_contains_hebrew"] == 1
    assert report["counts"]["hebrew_abbreviation_quote_lost"] == 1
    assert report["counts"]["export_missing_ai_verdict"] == 2
    assert report["findings"][0]["local_id"] == "work:bad"
    assert set(report["findings"][0]) == {
        "local_id", "entity_type", "label", "checks",
        "description_en", "existing_qid", "author_properties", "work_evidence",
    }


def test_audit_accepts_diagnostic_csv_fields(tmp_path: Path) -> None:
    source = tmp_path / "diagnostic.csv"
    source.write_text(
        "local_id,entity_type,existing_qid,approved,ai_verdict_json,label_he,description_en,statements_json\n"
        'work:good,work,Q123,true,"{""overall"":""pass""}",ספר,English work,"[{""property_id"":""P2093""}]"\n',
        encoding="utf-8",
    )

    report = audit(source)

    assert report["export_mode"] == "diagnostic"
    assert report["counts"] == {}


def test_audit_flags_one_missing_review_field_in_otherwise_diagnostic_json(tmp_path: Path) -> None:
    source = tmp_path / "partial-diagnostic.json"
    source.write_text(
        json.dumps({"items": [{"local_id": "ms:1", "approved": None}]}),
        encoding="utf-8",
    )

    report = audit(source)

    assert report["export_mode"] == "diagnostic"
    assert report["counts"] == {"export_missing_ai_verdict": 1}
