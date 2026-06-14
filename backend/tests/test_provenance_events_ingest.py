"""Phase 1 (web) — _extract_provenance_events from collapsed MARC keys.

The web ingest has two paths that must agree:
  - ``.mrc`` → desktop ``extract_all_data`` (fills ``provenance_events``)
  - TSV / JSON with ``<tag>$<sub>`` keys → ``_collapse_marc_subfields`` →
    ``_extract_provenance_events`` (this module)

Both reuse the same ``FieldHandlers`` helpers, so the event dicts are
byte-identical. These tests pin the collapsed-key half + idempotency.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.pipeline.marc_ingest import (  # noqa: E402
    _collapse_marc_subfields,
    _extract_provenance_events,
    extract_named_entities,
)


class TestExtractProvenanceEvents:
    def test_acquisition_from_541_subfields(self) -> None:
        rec = {
            "541$a": "Braginsky Collection",
            "541$b": "Zurich, Switzerland",
            "541$d": "1985",
        }
        _extract_provenance_events(rec)
        evs = rec["provenance_events"]
        assert len(evs) == 1
        assert evs[0]["type"] == "acquisition"
        assert evs[0]["place_text"] == "Zurich"
        assert evs[0]["year"] == 1985
        assert evs[0]["lat"] is None  # never fabricated at ingest

    def test_exhibition_from_583(self) -> None:
        rec = {"583$a": "exhibited", "583$j": "New York", "583$c": "1999"}
        _extract_provenance_events(rec)
        evs = rec["provenance_events"]
        assert evs[0]["type"] == "exhibition"
        assert evs[0]["place_text"] == "New York"
        assert evs[0]["year"] == 1999

    def test_conservation_default_action(self) -> None:
        rec = {"583$a": "conserved", "583$j": "Jerusalem"}
        _extract_provenance_events(rec)
        assert rec["provenance_events"][0]["type"] == "conservation"

    def test_repeated_583_pipe_separated(self) -> None:
        rec = {"583$a": "conserved|exhibited", "583$j": "Jerusalem|Paris"}
        _extract_provenance_events(rec)
        types = {e["place_text"]: e["type"] for e in rec["provenance_events"]}
        assert types == {"Jerusalem": "conservation", "Paris": "exhibition"}

    def test_no_event_when_no_place(self) -> None:
        rec = {"541$a": "Some donor"}  # no $b address, no 583
        _extract_provenance_events(rec)
        assert "provenance_events" not in rec

    def test_idempotent_when_already_populated(self) -> None:
        rec = {
            "541$b": "Zurich",
            "provenance_events": [{"type": "ownership", "place_text": "London"}],
        }
        _extract_provenance_events(rec)
        # Pre-existing list (e.g. from the .mrc path) is preserved untouched.
        assert rec["provenance_events"] == [{"type": "ownership", "place_text": "London"}]


class TestCollapseRunsExtraction:
    def test_collapse_marc_subfields_populates_events(self) -> None:
        rec = {
            "245$a": "Some MS",
            "541$b": "Poughkeepsie, NY 12601",
            "541$d": "1972",
        }
        _collapse_marc_subfields(rec)
        evs = rec.get("provenance_events") or []
        assert any(e["place_text"] == "Poughkeepsie" and e["year"] == 1972 for e in evs)


class TestEntityRoutingToKima:
    def test_event_places_yield_typed_place_roles(self) -> None:
        rec = {
            "provenance_events": [
                {"type": "acquisition", "place_text": "Zurich", "source_field": "541"},
                {"type": "conservation", "place_text": "Jerusalem", "source_field": "583"},
                {"type": "exhibition", "place_text": "New York", "source_field": "583"},
            ]
        }
        ents = extract_named_entities(rec)
        by_text = {e["text"]: e for e in ents if e["kind"] == "place"}
        assert by_text["Zurich"]["role"] == "acquisition_place"
        assert by_text["Jerusalem"]["role"] == "conservation_place"
        assert by_text["New York"]["role"] == "exhibition_place"
        # Every event place role ends with "_place" so authority.is_place fires.
        assert all(
            e["role"].endswith("_place")
            for e in ents
            if e["kind"] == "place" and e["text"] in {"Zurich", "Jerusalem", "New York"}
        )

    def test_is_place_predicate_accepts_event_roles(self) -> None:
        # Locks the authority.py contract: any "<x>_place" role is a place.
        for role in ("acquisition_place", "conservation_place",
                     "exhibition_place", "ownership_place"):
            assert role.endswith("_place")

    def test_event_place_skipped_when_empty(self) -> None:
        rec = {"provenance_events": [{"type": "acquisition", "place_text": ""}]}
        place_ents = [e for e in extract_named_entities(rec) if e["kind"] == "place"]
        assert place_ents == []
