"""HMO item AI verdict cache invalidation."""

from __future__ import annotations

from app.pipeline.hmo_item_verdict_cache import (
    HMO_ITEM_VERDICT_SCHEMA,
    hmo_item_verdict_input_fingerprint,
    hmo_item_verdict_query_summary,
    sanitise_stale_hmo_item_verdict,
)
from app.pipeline.inference_cache import canonical_hash


def test_query_summary_changes_when_labels_change() -> None:
  item = {
      "local_id": "QDraft_Person_1",
      "entity_type": "E21_Person",
      "control_numbers": ["990000403370205171"],
      "labels": {"en": "Moses Maimonides"},
      "descriptions": {"en": "Author linked to manuscript."},
      "claims": [],
      "shacl_issues": [],
      "_marc_context": {"authors": "Moses Maimonides"},
  }
  a = hmo_item_verdict_input_fingerprint(item, "gemini-3.5-flash")
  item["labels"] = {"en": "Maimonides"}
  b = hmo_item_verdict_input_fingerprint(item, "gemini-3.5-flash")
  assert a != b


def test_query_summary_changes_when_marc_context_changes() -> None:
  item = {
      "local_id": "QDraft_Person_1",
      "entity_type": "E21_Person",
      "control_numbers": ["990000403370205171", "990000880710205171"],
      "labels": {"en": "Shared Author"},
      "descriptions": {"en": "Author."},
      "claims": [],
      "shacl_issues": [],
      "_marc_context": {"authors": "Shared Author"},
  }
  a = hmo_item_verdict_input_fingerprint(item, "gemini-3.5-flash")
  item["_marc_context"] = {
      "authors": "Shared Author",
      "contributors": "Scribe X",
  }
  b = hmo_item_verdict_input_fingerprint(item, "gemini-3.5-flash")
  assert a != b


def test_query_summary_includes_schema_salt() -> None:
  summary = hmo_item_verdict_query_summary(
      {"local_id": "x", "labels": {}, "descriptions": {}},
      "gemini-3.5-flash",
  )
  assert summary["hmo_item_verdict_schema"] == HMO_ITEM_VERDICT_SCHEMA


def test_sanitise_stale_hmo_item_verdict_hides_mismatched_key() -> None:
  item = {
      "local_id": "QDraft_Person_1",
      "entity_type": "E21_Person",
      "control_numbers": ["990000403370205171"],
      "labels": {"en": "Moses Maimonides"},
      "descriptions": {"en": "Author."},
      "claims": [],
      "shacl_issues": [],
      "_marc_context": {"authors": "Moses Maimonides"},
      "ai_verdict": {
          "overall": "full",
          "cache_key": "stale-eval-agent-prompt-hash",
          "model": "gemini-3.5-flash",
          "evaluator": "hmo_wikibase_item",
      },
  }
  assert sanitise_stale_hmo_item_verdict(item) is None


def test_sanitise_stale_hmo_item_verdict_keeps_matching_key() -> None:
  item = {
      "local_id": "QDraft_Person_1",
      "entity_type": "E21_Person",
      "control_numbers": ["990000403370205171"],
      "labels": {"en": "Moses Maimonides"},
      "descriptions": {"en": "Author."},
      "claims": [],
      "shacl_issues": [],
      "_marc_context": {"authors": "Moses Maimonides"},
  }
  fp = hmo_item_verdict_input_fingerprint(item, "gemini-3.5-flash")
  item["ai_verdict"] = {
      "overall": "full",
      "cache_key": fp,
      "model": "gemini-3.5-flash",
      "evaluator": "hmo_wikibase_item",
  }
  kept = sanitise_stale_hmo_item_verdict(item)
  assert kept is not None
  assert kept["cache_key"] == fp
  assert canonical_hash(hmo_item_verdict_query_summary(item, "gemini-3.5-flash")) == fp
