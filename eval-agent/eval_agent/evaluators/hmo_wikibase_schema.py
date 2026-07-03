"""HMO Wikibase schema bootstrap evaluator.

Judges each ontology class/property the schema bootstrap created (or
would create in a dry run) on ``mhm-hmo.wikibase.cloud`` against the
HMO ontology semantics and, for the small overlap, Wikidata's own
manuscript-modeling conventions. Reads ``hmo_wikibase_schema.json``
only — schema entries are global (one per ontology URI), not tied to
a MARC record, so ``marc_record`` is typically empty and
``marc_context`` stays empty in the rendered prompt.
"""

from __future__ import annotations

from typing import Any, Iterable

from eval_agent.evaluators._base import Candidate, Evaluator
from eval_agent.ingest import hmo_wikibase_schema


class HmoWikibaseSchemaEvaluator(Evaluator):
    id = "hmo_wikibase_schema"
    sub_types = ["class", "property"]
    rubric_name = "hmo_wikibase_schema.md"
    marc_field_keys: list[str] = []

    def extract_candidates(
        self,
        *,
        ner_record: dict[str, Any],
        marc_record: dict[str, Any],
        threshold: float,
    ) -> Iterable[Candidate]:
        entry = ner_record
        entity_kind = str(entry.get("entity_kind") or "property")
        payload = {
            "_local_id": hmo_wikibase_schema.local_id(entry),
            "ontology_uri": entry.get("ontology_uri"),
            "entity_kind": entity_kind,
            "label": entry.get("label"),
            "datatype": entry.get("datatype"),
            "wikibase_id": entry.get("wikibase_id"),
            "status": entry.get("status"),
        }
        yield Candidate(
            record_id=payload["_local_id"],
            evaluator_id=self.id,
            sub_type=entity_kind,
            payload=payload,
            confidence=1.0,
            marc_context={},
            grounded=None,
            role_fields=[],
            exists_in=[],
            grounded_field=None,
        )

    def build_prompt(self, candidate: Candidate) -> str:
        p = candidate.payload
        block = (
            "Model: HMO Wikibase schema bootstrap\n"
            "Prediction:\n"
            f"  ontology URI:   {p.get('ontology_uri', '')}\n"
            f"  entity kind:    {p.get('entity_kind', '')}\n"
            f"  label:          {p.get('label', '')}\n"
            f"  datatype:       {p.get('datatype') or '(class — no datatype)'}\n"
            f"  wikibase id:    {p.get('wikibase_id') or '(dry-run — not yet created)'}\n"
        )
        return self.render_prompt(candidate, prediction_block=block)
