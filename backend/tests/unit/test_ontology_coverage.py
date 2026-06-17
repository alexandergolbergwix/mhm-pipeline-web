"""Mirror of desktop ontology coverage tests (vendored converter)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

PIPELINE_ROOT = BACKEND_ROOT.parent.parent / "pipeline"
FIXTURE_DIR = PIPELINE_ROOT / "data" / "fixtures" / "ontology_golden"
ONTOLOGY_PATH = BACKEND_ROOT / "ontology" / "hebrew-manuscripts.ttl"

from converter.rdf.ontology_coverage import (  # noqa: E402
    build_coverage_report,
    check_against_baseline,
    load_ontology_terms,
)
from converter.transformer.mapper import MarcToRdfMapper  # noqa: E402


def test_web_ontology_inventory_matches_desktop() -> None:
    inv = load_ontology_terms(ONTOLOGY_PATH)
    assert inv.class_count == 73
    assert inv.property_count >= 238


def test_golden_fixture_meets_baseline_when_present() -> None:
    if not FIXTURE_DIR.exists():
        return
    records_path = FIXTURE_DIR / "records.json"
    expected_path = FIXTURE_DIR / "expected_coverage.json"
    if not records_path.exists() or not expected_path.exists():
        return
    records = json.loads(records_path.read_text(encoding="utf-8"))
    graph = MarcToRdfMapper().map_json_records(records)
    report = build_coverage_report(None, ONTOLOGY_PATH, graph=graph)
    baseline = json.loads(expected_path.read_text(encoding="utf-8"))
    assert check_against_baseline(report, baseline) == []
