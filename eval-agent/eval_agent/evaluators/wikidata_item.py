"""Wikidata Studio item evaluator.

Judges each serialized Wikidata item against the MARC context that fed
the Studio projection. Reads ``wikidata_items.json`` only.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from eval_agent.evaluators._base import Candidate, Evaluator
from eval_agent.ingest import marc_extract, wikidata_items


class WikidataItemEvaluator(Evaluator):
    id = "wikidata_item"
    sub_types = ["manuscript", "person", "work", "item"]
    rubric_name = "wikidata_item.md"
    marc_field_keys = [
        "title", "authors", "contributors", "subjects", "provenance",
        "notes", "dates", "place", "related_places", "languages",
        "material", "extent", "shelfmark", "colophon_text",
    ]

    def extract_candidates(
        self,
        *,
        ner_record: dict[str, Any],
        marc_record: dict[str, Any],
        threshold: float,
    ) -> Iterable[Candidate]:
        conf = wikidata_items.confidence(ner_record)
        if conf < threshold:
            return

        local_id = wikidata_items.local_id(ner_record, 0)
        payload = {
            "_local_id": local_id,
            "entity_type": ner_record.get("entity_type") or "item",
            "labels": ner_record.get("labels") or {},
            "descriptions": ner_record.get("descriptions") or {},
            "aliases": ner_record.get("aliases") or {},
            "statements": wikidata_items.compact_statements(ner_record),
            "statement_count": len(ner_record.get("statements") or []),
            "existing_qid": ner_record.get("existing_qid"),
            "validation_issues": ner_record.get("validation_issues") or [],
        }
        control_number = wikidata_items.control_number(ner_record)
        yield Candidate(
            record_id=local_id,
            evaluator_id=self.id,
            sub_type=str(ner_record.get("entity_type") or "item"),
            payload=payload,
            confidence=conf,
            marc_context=marc_extract.project(marc_record, self.marc_field_keys),
            grounded=None,
            role_fields=[],
            exists_in=[],
            grounded_field=control_number or None,
        )

    def build_prompt(self, candidate: Candidate) -> str:
        p = candidate.payload
        statements_json = json.dumps(
            p.get("statements") or [], ensure_ascii=False, indent=2
        )
        issues_json = json.dumps(
            p.get("validation_issues") or [], ensure_ascii=False, indent=2
        )
        block = (
            "Model: Wikidata Studio item projection\n"
            "Prediction:\n"
            f"  local id:          {p.get('_local_id', '')}\n"
            f"  entity type:       {p.get('entity_type', '')}\n"
            f"  labels:            {json.dumps(p.get('labels') or {}, ensure_ascii=False)}\n"
            "  descriptions:      "
            f"{json.dumps(p.get('descriptions') or {}, ensure_ascii=False)}\n"
            f"  aliases:           {json.dumps(p.get('aliases') or {}, ensure_ascii=False)}\n"
            f"  existing QID:      {p.get('existing_qid') or '(new item)'}\n"
            f"  statement count:   {p.get('statement_count', 0)}\n"
            f"  statements sample:\n{statements_json}\n"
            f"  validation issues:\n{issues_json}\n"
        )
        return self.render_prompt(candidate, prediction_block=block)
