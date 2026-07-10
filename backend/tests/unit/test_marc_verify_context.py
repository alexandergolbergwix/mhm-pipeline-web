"""MARC merge helpers for verify cache keys."""

from __future__ import annotations

from app.pipeline.marc_verify_context import (
    index_marc_records,
    marc_context_for_item,
    merge_marc_records,
)


def test_merge_marc_records_unions_authors() -> None:
  rec_a = {
      "_control_number": "990000403370205171",
      "title": "MS A",
      "authors": [{"name": "Author A"}],
  }
  rec_b = {
      "_control_number": "990000880710205171",
      "authors": [{"name": "Author B"}],
  }
  merged = merge_marc_records([rec_a, rec_b], primary=rec_a)
  names = {a["name"] for a in merged["authors"]}
  assert names == {"Author A", "Author B"}
  assert merged["title"] == "MS A"


def test_marc_context_for_item_uses_all_control_numbers() -> None:
  index = index_marc_records([
      {
          "_control_number": "990000403370205171",
          "authors": [{"name": "Shared Author"}],
      },
      {
          "_control_number": "990000880710205171",
          "contributors": [{"name": "Scribe X", "role": "scribe"}],
      },
  ])
  ctx = marc_context_for_item(
      {
          "control_numbers": [
              "990000880710205171",
              "990000403370205171",
          ],
          "source_uri": "http://example#Person_in_990000880710205171",
      },
      index,
  )
  assert "Shared Author" in ctx["authors"]
  assert "Scribe X" in ctx["contributors"]


def test_marc_context_joins_quoted_control_number() -> None:
  # Persisted _control_number carries literal quotes; a clean item control
  # number must still join so the judge gets real MARC (not empty context).
  index = index_marc_records([
      {"_control_number": '"990000827290205171"', "title": "פיוטים ושירים"},
  ])
  ctx = marc_context_for_item(
      {"control_numbers": ["990000827290205171"],
       "source_uri": "http://x#Expression_in_990000827290205171"},
      index,
  )
  assert ctx.get("title") == "פיוטים ושירים"
