"""HMO Wikibase Studio item evaluator."""

from __future__ import annotations

import json
from typing import Any, Iterable

from eval_agent.evaluators._base import Candidate, Evaluator
from eval_agent.ingest import hmo_wikibase_items, marc_extract


class HmoWikibaseItemEvaluator(Evaluator):
    id = "hmo_wikibase_item"
    sub_types = ["manuscript", "person", "work", "place", "item"]
    rubric_name = "hmo_wikibase_item.md"
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
        conf = hmo_wikibase_items.confidence(ner_record)
        if conf < threshold:
            return
        local_id = hmo_wikibase_items.local_id(ner_record, 0)
        payload = {
            "_local_id": local_id,
            "labels": ner_record.get("labels") or {},
            "descriptions": ner_record.get("descriptions") or {},
            "class_qid": ner_record.get("class_qid"),
            "source_uri": ner_record.get("source_uri"),
            "wikibase_id": ner_record.get("wikibase_id"),
            "claims": hmo_wikibase_items.compact_statements(ner_record),
            "shacl_issues": ner_record.get("shacl_issues") or [],
        }
        control_number = hmo_wikibase_items.control_number(ner_record)
        yield Candidate(
            record_id=local_id,
            evaluator_id=self.id,
            sub_type="item",
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
        claims_json = json.dumps(p.get("claims") or [], ensure_ascii=False, indent=2)
        issues_json = json.dumps(p.get("shacl_issues") or [], ensure_ascii=False, indent=2)
        block = (
            "Model: HMO Wikibase Studio item projection\n"
            "Prediction:\n"
            f"  local id:        {p.get('_local_id', '')}\n"
            f"  class QID:       {p.get('class_qid') or ''}\n"
            f"  source URI:      {p.get('source_uri') or ''}\n"
            f"  live Wikibase:   {p.get('wikibase_id') or '(not uploaded)'}\n"
            f"  labels:          {json.dumps(p.get('labels') or {}, ensure_ascii=False)}\n"
            f"  descriptions:    "
            f"{json.dumps(p.get('descriptions') or {}, ensure_ascii=False)}\n"
            f"  claims sample:\n{claims_json}\n"
            f"  SHACL issues:\n{issues_json}\n"
        )
        return self.render_prompt(candidate, prediction_block=block)
