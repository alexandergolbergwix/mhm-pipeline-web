"""WP9 coverage harness + WP7 mapping corrections.

Rule W-67 (docs/architecture/rules/wikidata-studio.md) is the reason the
harness exists at all: projection changes used to move fields to the wrong
property and nobody measured it. Rule WP7.1 in particular — ``710`` is the
holding library, so it maps to ``P195`` (collection), never ``P50`` (author).

The corpus fixture is small and lives in-repo so CI never reaches the network
or the external pipeline checkout. Coverage numbers come from a recorded
claims snapshot; ``scripts.marc_wikidata_coverage.py --projected`` regenerates
both the snapshot and the baseline when a work package lands.
"""
from __future__ import annotations

import json
from pathlib import Path

from converter.wikidata.marc_field_map import (
    BY_TAG,
    EVIDENCE,
    MAP,
    MARC_FIELD_TARGETS,
    OUT_OF_MODEL,
    PROPERTY_TARGETS,
    ledger_rows,
)
from scripts.marc_wikidata_coverage import (
    ClaimsIndex,
    entry_works,
    load_corpus,
    main,
    measure,
    regressions,
    work_key,
)

FIXTURES = Path(__file__).parent / "fixtures"
CORPUS = FIXTURES / "marc_coverage_corpus.tsv"
CLAIMS = FIXTURES / "marc_coverage_claims.json"
BASELINE = FIXTURES / "marc_coverage_baseline.json"


def _result():
    rows, _ = load_corpus(CORPUS)
    index = ClaimsIndex.from_json(CLAIMS)
    return measure(rows, index)


def test_every_field_has_a_deliberate_fate() -> None:
    """WP8: no field stays silently absent."""
    for row in ledger_rows():
        assert row["fate"] in {MAP, EVIDENCE, OUT_OF_MODEL}, row
        # A label maps onto aliases/labels and a linked-tag field projects
        # under its target tag; neither is a statement with a PID of its own.
        if row["pids"] == "→ linked tag":
            assert BY_TAG[row["tag"]].linked_tag
            continue
        if row["fate"] == MAP and row["kind"] in {"claim", "ref"}:
            assert row["pids"] != "—", f"{row['tag']} maps but names no property"


def test_wp7_710_maps_to_collection_not_author() -> None:
    """WP7.1: a holding library is a collection (P195), never an author (P50)."""
    target = BY_TAG["710$a"]
    assert target.pids == ("P195",)
    assert "P50" not in target.pids
    assert target.target_coverage is not None


def test_wp7_035_shares_the_001_identity_channel() -> None:
    """WP7.2: 035 falls back into the same P3959 channel as 001."""
    assert BY_TAG["001"].pids == BY_TAG["035$a"].pids == ("P3959",)


def test_wp7_rda_types_are_declared_out_of_model() -> None:
    """WP7.4: RDA 336-338 are an explicit decision, not a silent gap."""
    for tag in ("336$a", "337$a", "338$a"):
        assert BY_TAG[tag].fate == OUT_OF_MODEL
        assert BY_TAG[tag].pids == ()


def test_wp7_linked_880_projects_as_its_linked_tag() -> None:
    """WP7.3: 880 rows are not a field of their own."""
    assert BY_TAG["880"].linked_tag
    assert BY_TAG["880"].pids == ()
    assert "linked" in BY_TAG["880"].note.lower()


def test_identity_layer_is_fully_covered() -> None:
    result = _result()
    for row in result["fields"]:
        if row["tag"] in {"001", "035$a", "041$a", "245$a", "245$b", "260$c", "300$a"}:
            assert row["coverage"] == 1.0, row


def test_authorship_is_measured_on_works_not_manuscripts() -> None:
    """WP1: 100/700 authorship resolves through P1574 to the work."""
    result = _result()
    by_tag = {row["tag"]: row for row in result["fields"]}
    assert by_tag["100$a"]["pids"] == ["P50", "P2093"]
    assert by_tag["700$a"]["pids"] == ["P50", "P2093"]
    assert by_tag["100$a"]["with_field"] > 0
    # The join through P1574 must actually reach the work. A missing
    # __LOCAL: prefix on the lookup key silently reported 0% for every
    # work-layer field.
    assert by_tag["100$a"]["covered"] > 0, by_tag["100$a"]


def test_work_reference_keys_resolve_through_the_local_prefix() -> None:
    """A projected work is `__LOCAL:<local_id>` but is filed under `<local_id>`."""
    assert work_key("__LOCAL:work:עץ_חיים") == "work:עץ_חיים"
    assert work_key("Q131068") == "Q131068"
    entry = {
        "claims": {"P1574": ["__LOCAL:work:עץ_חיים"]},
        "works": {"work:עץ_חיים": {"P50": ["Q123"]}},
    }
    assert entry_works(entry) == [{"P50": ["Q123"]}]


def test_property_share_counts_each_record_once() -> None:
    """A work shared by many manuscripts must not inflate share past 100%."""
    rows, _ = load_corpus(CORPUS)
    result = measure(rows, ClaimsIndex.from_json(CLAIMS))
    for row in result["properties"]:
        assert 0.0 <= row["share"] <= 1.0 + 1e-9, row
        assert row["items"] <= result["records"], row


def test_coverage_does_not_regress_below_baseline() -> None:
    """The CI gate: a projection change may not silently drop coverage."""
    result = _result()
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    drops = regressions(result, baseline)
    assert drops == [], drops


def test_baseline_records_the_plan_targets() -> None:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    baseline_targets = {
        row["tag"]: row["target"] for row in baseline["fields"]
    }
    for tag, target in {t.tag: t.target_coverage for t in MARC_FIELD_TARGETS}.items():
        if target is not None and tag in baseline_targets:
            assert baseline_targets[tag] == target, tag


def test_property_targets_cover_every_plan_gate() -> None:
    for pid in ("P1574", "P50", "P2093", "P921", "P136", "P2048", "P186",
                "P1071", "P7153", "P7535", "P953"):
        assert pid in PROPERTY_TARGETS, pid


def test_cli_runs_clean_against_the_fixture(tmp_path) -> None:
    exit_code = main([
        str(CORPUS), "--claims", str(CLAIMS),
        "--json", str(tmp_path / "out.json"),
    ])
    assert exit_code == 0
    payload = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert payload["records"] == 15
    assert payload["matched_items"] == 15


def test_cli_fails_on_regression(tmp_path) -> None:
    """A hand-raised baseline must make the harness exit non-zero."""
    result = _result()
    raised = json.loads(json.dumps(result))
    for row in raised["fields"]:
        if row["coverage"] is not None:
            row["coverage"] = 1.0
    for row in raised["properties"]:
        row["share"] = 1.0
    raised_path = tmp_path / "raised.json"
    raised_path.write_text(json.dumps(raised), encoding="utf-8")
    exit_code = main([
        str(CORPUS), "--claims", str(CLAIMS), "--baseline", str(raised_path),
    ])
    assert exit_code == 1
