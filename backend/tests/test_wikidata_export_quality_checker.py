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


def _work_with_title(local_id: str, title: str) -> dict:
    return {
        "local_id": local_id,
        "entity_type": "work",
        "approved": True,
        "ai_verdict": {"overall": "full"},
        "label": title,
        "statements": [{"property_id": "P1476", "value": title}],
    }


def test_gershayim_in_a_title_claim_is_not_quote_noise(tmp_path: Path) -> None:
    """Rule W-76 — רש"י is the title, not an unbalanced ISBD wrapper."""
    source = tmp_path / "titles.json"
    source.write_text(
        json.dumps(
            {
                "items": [
                    _work_with_title("work:gershayim", 'פרוש רש"י'),
                    _work_with_title("work:geresh", "מאמרי חז\"ל הפותחים בג' דברים"),
                    _work_with_title("work:wrapped", '"דרושים."'),
                    _work_with_title("work:doubled", 'תקכ""א'),
                ],
            },
        ),
        encoding="utf-8",
    )

    report = audit(source)

    assert report["counts"]["title_claim_has_quote_wrappers"] == 2
    flagged = {
        f["local_id"]
        for f in report["findings"]
        if "title_claim_has_quote_wrappers" in f["checks"]
    }
    assert flagged == {"work:wrapped", "work:doubled"}


class TestDuplicateCoverageChecks:
    """Rules W-144 / W-145 / W-146 — the gate must see a missing check."""

    def test_a_work_with_no_probe_is_reported_as_a_coverage_gap(self) -> None:
        from scripts.check_wikidata_export_quality import _check_row

        row = _check_row({
            "entity_type": "work",
            "statements": [{"property_id": "P1476", "value": "הגדה של פסח"}],
        })
        assert "work_create_without_duplicate_probe" in (row or {}).get("checks", [])

    def test_a_probed_work_is_clean(self) -> None:
        from scripts.check_wikidata_export_quality import _check_row

        row = _check_row({
            "entity_type": "work",
            "statements": [{"property_id": "P1476", "value": "x"}],
            "duplicate_check": {"status": "absent", "candidates": []},
        })
        assert "work_create_without_duplicate_probe" not in (row or {}).get("checks", [])

    def test_a_resolved_holder_without_the_composite_probe_is_flagged(self) -> None:
        from scripts.check_wikidata_export_quality import _check_row

        row = _check_row({
            "entity_type": "manuscript",
            "statements": [
                {"property_id": "P195", "value": "Q1028334"},
                {"property_id": "P217", "value": "F 18760"},
            ],
            "duplicate_check": {
                "status": "absent",
                "candidates": [],
                "probed": [{"pid": "P3959", "value": "99000"}],
            },
        })
        assert "manuscript_missing_holder_shelfmark_probe" in (row or {}).get("checks", [])

    def test_a_top_level_candidate_still_blocks(self) -> None:
        from scripts.check_wikidata_export_quality import _check_row

        row = _check_row({
            "entity_type": "manuscript",
            "statements": [{"property_id": "P3959", "value": "99000"}],
            "duplicate_check": {"status": "candidates_found", "candidates": [{"qid": "Q1"}]},
        })
        assert "create_would_duplicate_existing_item" in (row or {}).get("checks", [])

    def test_p2093_beside_a_real_author_item_is_a_defect(self) -> None:
        from scripts.check_wikidata_export_quality import _check_row

        row = _check_row({
            "entity_type": "work",
            "statements": [
                {"property_id": "P1476", "value": "x"},
                {"property_id": "P2093", "value": "משה בן מימון"},
                {"property_id": "P50", "value": "__LOCAL:viaf:1"},
            ],
            "duplicate_check": {"status": "absent"},
        })
        assert "author_name_string_beside_author_item" in (row or {}).get("checks", [])

    def test_p2093_alone_is_policy_correct_and_not_a_defect(self) -> None:
        # Wikidata:Notability — with no identifier there is no item to link to,
        # and P2093 is exactly what the guidance prescribes.
        from scripts.check_wikidata_export_quality import _check_row

        row = _check_row({
            "entity_type": "work",
            "statements": [
                {"property_id": "P1476", "value": "x"},
                {"property_id": "P2093", "value": "משה בן מימון"},
            ],
            "duplicate_check": {"status": "absent"},
        })
        assert "author_name_string_beside_author_item" not in (row or {}).get("checks", [])
