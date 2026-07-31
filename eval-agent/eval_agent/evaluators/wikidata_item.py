"""Wikidata Studio item evaluator.

Judges each serialized Wikidata item against the MARC context that fed
the Studio projection. Reads ``wikidata_items.json`` only.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from eval_agent.evaluators._base import Candidate, Evaluator
from eval_agent.ingest import marc_extract, wikidata_items
from eval_agent.skills.wikidata_manuscripts import skill_context_from_payload


class WikidataItemEvaluator(Evaluator):
    id = "wikidata_item"
    sub_types = ["manuscript", "person", "work", "item"]
    rubric_name = "wikidata_item.md"
    marc_field_keys = [
        "title", "authors", "contributors", "subjects", "provenance",
        "notes", "dates", "place", "related_places", "languages",
        "material", "extent", "shelfmark", "colophon_text",
        "contents", "work_mentions", "genres", "genre_entries",
        "holding_institution", "catalog_references", "related_works",
        "source_field", "505$a", "500$a", "561$a",
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
            "semantic_type": ner_record.get("semantic_type") or "",
            "labels": ner_record.get("labels") or {},
            "descriptions": ner_record.get("descriptions") or {},
            "aliases": ner_record.get("aliases") or {},
            "statements": wikidata_items.compact_statements(ner_record),
            "statement_count": len(ner_record.get("statements") or []),
            "existing_qid": ner_record.get("existing_qid"),
            "hmo_wikibase_id": ner_record.get("hmo_wikibase_id"),
            "source_uri": ner_record.get("source_uri"),
            "validation_issues": ner_record.get("validation_issues") or [],
            "authority_evidence": ner_record.get("authority_evidence") or [],
            "work_candidate_evidence": ner_record.get("work_candidate_evidence") or {},
            "local_reference_targets": ner_record.get("local_reference_targets") or {},
            "verify_evidence": ner_record.get("verify_evidence") or {},
            "record_ids": wikidata_items.control_numbers(ner_record),
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
        evidence = p.get("verify_evidence") if isinstance(p.get("verify_evidence"), dict) else {}
        viaf_json = json.dumps(evidence.get("viaf") or {}, ensure_ascii=False, indent=2)
        mazal_json = json.dumps(evidence.get("mazal") or {}, ensure_ascii=False, indent=2)
        duplicate_check = {}
        existing_pack = evidence.get("wikidata_existing")
        if isinstance(existing_pack, dict):
            duplicate_check = existing_pack.get("duplicate_check") or {}
        duplicate_json = json.dumps(duplicate_check, ensure_ascii=False, indent=2)
        llm_proposals_json = json.dumps(
            evidence.get("llm_proposals") or {}, ensure_ascii=False, indent=2,
        )
        wd_existing_json = json.dumps(
            evidence.get("wikidata_existing") or {
                "existing_qid": p.get("existing_qid"),
            },
            ensure_ascii=False,
            indent=2,
        )
        hmo_json = json.dumps(
            evidence.get("hmo_wikibase") or {
                "hmo_wikibase_id": p.get("hmo_wikibase_id"),
                "source_uri": p.get("source_uri"),
            },
            ensure_ascii=False,
            indent=2,
        )
        authority_json = json.dumps(p.get("authority_evidence") or [], ensure_ascii=False, indent=2)
        work_evidence_json = json.dumps(
            p.get("work_candidate_evidence") or evidence.get("work_candidate_evidence") or {},
            ensure_ascii=False,
            indent=2,
        )
        local_targets_json = json.dumps(
            p.get("local_reference_targets") or evidence.get("local_reference_targets") or {},
            ensure_ascii=False,
            indent=2,
        )
        claim_sources_json = json.dumps(
            evidence.get("claim_sources") or {}, ensure_ascii=False, indent=2,
        )
        marc_note = (
            "present"
            if (evidence.get("marc_present") or candidate.marc_context)
            else "ABSENT — do not invent MARC facts; still use VIAF/Mazal/Wikidata/Wikibase packs"
        )
        block = (
            "Model: Wikidata Studio item projection\n"
            "Prediction:\n"
            f"  local id:          {p.get('_local_id', '')}\n"
            f"  entity type:       {p.get('entity_type', '')}\n"
            f"  semantic subtype:  {p.get('semantic_type') or '(none)'}\n"
            f"  record ids:        {json.dumps(p.get('record_ids') or [], ensure_ascii=False)}\n"
            f"  labels:            {json.dumps(p.get('labels') or {}, ensure_ascii=False)}\n"
            "  descriptions:      "
            f"{json.dumps(p.get('descriptions') or {}, ensure_ascii=False)}\n"
            f"  aliases:           {json.dumps(p.get('aliases') or {}, ensure_ascii=False)}\n"
            f"  existing QID:      {p.get('existing_qid') or '(new item)'}\n"
            f"  statement count:   {p.get('statement_count', 0)}\n"
            f"  statements sample:\n{statements_json}\n"
            f"  validation issues:\n{issues_json}\n"
            "Evidence channels (all first-class — MARC is necessary but not sufficient):\n"
            f"  MARC slice status: {marc_note}\n"
            "  per-claim MARC provenance (PID -> the source field text that\n"
            "  supports it; a claim listed here WITH evidence is supported —\n"
            "  do not call it unsourced):\n"
            f"{claim_sources_json}\n"
            f"  VIAF pack:\n{viaf_json}\n"
            f"  Mazal / NLI pack:\n{mazal_json}\n"
            f"  Existing Wikidata pack:\n{wd_existing_json}\n"
            "  live duplicate check (Wikidata Action API; status\n"
            "  candidates_found = an item with this identifier ALREADY EXISTS;\n"
            "  absent = none found; unavailable/skipped/not_run = UNKNOWN, do not\n"
            "  conclude the item is new):\n"
            f"{duplicate_json}\n"
            "  LLM extraction proposals (span-grounded CANDIDATES mined from\n"
            "  MARC provenance prose — NOT evidence and NOT claims on the item.\n"
            "  Never treat a proposal as support for a statement, and never\n"
            "  fault the item for not carrying one):\n"
            f"{llm_proposals_json}\n"
            f"  HMO Wikibase pack:\n{hmo_json}\n"
            f"  authority evidence (raw):\n{authority_json}\n"
            f"  work candidate evidence:\n{work_evidence_json}\n"
            f"  local reference targets:\n{local_targets_json}\n"
            "  WPM Data Model: "
            "https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts/Data_Model\n"
            "  (also injected below as the SKILL block)\n"
        )
        return self.render_prompt(candidate, prediction_block=block)

    def skill_context(self, candidate: Candidate) -> str:
        return skill_context_from_payload(
            channel="wikidata",
            payload=candidate.payload,
        )
