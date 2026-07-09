"""HMO Wikibase Studio item evaluator."""

from __future__ import annotations

import json
from typing import Any, Iterable

from eval_agent.evaluators._base import Candidate, Evaluator
from eval_agent.ingest import hmo_wikibase_items, marc_extract
from eval_agent.ingest.shacl_gate import blocking_shacl_issues

_STRUCTURAL_ENTITY_TYPES = frozenset({
    "CatalogStep",
    "EvidenceStep",
    "EvidenceChain",
    "Evidence",
    "PhilologicalView",
    "BibliographicParadigm",
    "PhilologicalParadigm",
    "ViewType",
})


class HmoWikibaseItemEvaluator(Evaluator):
    id = "hmo_wikibase_item"
    sub_types = ["manuscript", "person", "work", "place", "item"]
    rubric_name = "hmo_wikibase_item.md"
    marc_field_keys = [
        "title", "authors", "contributors", "subjects", "provenance",
        "notes", "dates", "place", "related_places", "languages",
        "material", "extent", "shelfmark", "colophon_text", "contents",
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
        entity_type = str(ner_record.get("entity_type") or "")
        control_numbers = hmo_wikibase_items.control_numbers(ner_record)
        shacl_issues = ner_record.get("shacl_issues") or []
        blocking = blocking_shacl_issues(shacl_issues)
        payload = {
            "_local_id": local_id,
            "labels": ner_record.get("labels") or {},
            "descriptions": ner_record.get("descriptions") or {},
            "entity_type": entity_type,
            "class_qid": ner_record.get("class_qid"),
            "control_numbers": control_numbers,
            "source_uri": ner_record.get("source_uri"),
            "wikibase_id": ner_record.get("wikibase_id"),
            "claims": hmo_wikibase_items.compact_statements(ner_record),
            "shacl_issues": shacl_issues,
            "blocking_shacl": bool(blocking),
        }
        primary_cn = hmo_wikibase_items.primary_control_number(ner_record)
        marc_context = marc_extract.project(marc_record, self.marc_field_keys)
        grounded = bool(marc_context) or bool(primary_cn)
        role_fields: list[str] = []
        if entity_type in {"E21_Person", "E74_Group"}:
            role_fields = ["authors", "contributors", "subjects", "colophon_text"]
        elif entity_type in {"F1_Work", "F2_Expression"}:
            role_fields = ["title", "contents", "notes"]
        elif entity_type in {"E53_Place"}:
            role_fields = ["place", "related_places", "provenance"]
        elif entity_type in _STRUCTURAL_ENTITY_TYPES:
            role_fields = []

        yield Candidate(
            record_id=local_id,
            evaluator_id=self.id,
            sub_type="item",
            payload=payload,
            confidence=conf,
            marc_context=marc_context,
            grounded=grounded if marc_context else None,
            role_fields=role_fields,
            exists_in=[],
            grounded_field=primary_cn or None,
        )

    def build_prompt(self, candidate: Candidate) -> str:
        p = candidate.payload
        claims_json = json.dumps(p.get("claims") or [], ensure_ascii=False, indent=2)
        issues_json = json.dumps(p.get("shacl_issues") or [], ensure_ascii=False, indent=2)
        block = (
            "Model: HMO Wikibase Studio item projection\n"
            "Prediction:\n"
            f"  local id:        {p.get('_local_id', '')}\n"
            f"  entity type:     {p.get('entity_type') or ''}\n"
            f"  class QID:       {p.get('class_qid') or ''}  (HMO Wikibase — NOT Wikidata)\n"
            f"  control numbers: {json.dumps(p.get('control_numbers') or [], ensure_ascii=False)}\n"
            f"  source URI:      {p.get('source_uri') or ''}\n"
            f"  live Wikibase:   {p.get('wikibase_id') or '(not uploaded)'}\n"
            f"  labels:          {json.dumps(p.get('labels') or {}, ensure_ascii=False)}\n"
            f"  descriptions:    "
            f"{json.dumps(p.get('descriptions') or {}, ensure_ascii=False)}\n"
            f"  claims sample:\n{claims_json}\n"
            f"  SHACL issues:\n{issues_json}\n"
        )
        return self.render_prompt(candidate, prediction_block=block)

    def format_grounding(self, candidate: Candidate) -> str:
        entity_type = str(candidate.payload.get("entity_type") or "")
        blocking = candidate.payload.get("blocking_shacl")
        if blocking:
            return (
                "  STATE = SHACL BLOCKED — Violation/Error issues are present.\n"
                "  overall = fail, role_ok = no. Do not approve."
            )
        if entity_type in _STRUCTURAL_ENTITY_TYPES:
            return (
                "  STATE = STRUCTURAL — epistemology / view / cataloging step entity.\n"
                "  Verify internal consistency + non-generic descriptions.\n"
                "  role_ok = n/a unless SHACL blocking issues are present.\n"
                "  Do not fail solely because the label is a system identifier."
            )
        control_numbers = candidate.payload.get("control_numbers") or []
        merge_note = ""
        if len(control_numbers) > 1:
            merge_note = (
                "\n  MARC context is merged from all linked control numbers — verify\n"
                "  persons/works against the union of authors/title/contents/notes."
            )
        if entity_type == "Codicological_Unit":
            return (
                "  STATE = CODICOLOGICAL UNIT — English MS-scoped system labels such as\n"
                "  'Main codicological unit of manuscript …' are intentional.\n"
                "  name_ok = yes when the description names the MS and content scope."
                + merge_note
            )
        if entity_type == "F2_Expression":
            return (
                "  STATE = EXPRESSION — short scholarly title in the label is correct;\n"
                "  manuscript/folio scope belongs in the description, not the label.\n"
                "  name_ok = yes when title matches MARC 245/505 and description is substantive."
                + merge_note
            )
        if candidate.marc_context:
            return (
                "  STATE = MANUSCRIPT-SCOPED — MARC context is present for the linked\n"
                "  control number(s). Verify labels/claims against those fields.\n"
                "  Derived entities (person/work/unit) need not appear verbatim in MARC\n"
                "  when the name/title is supported by authors/title/contents/notes."
                + merge_note
            )
        return super().format_grounding(candidate)
