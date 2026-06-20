"""Authority-resolution (Stage 3) evaluator.

Judges each Mazal / VIAF / Wikidata / KIMA match the pipeline assigned to
an entity: is this the correct authority record for this name, given the
manuscript's MARC context? Reads ``authority_enriched.json`` records
(which carry the full MARC fields PLUS ``marc_authority_matches``), so the
``ner_record`` passed in IS the authority record.
"""

from __future__ import annotations

from typing import Any, Iterable

from eval_agent.evaluators._base import Candidate, Evaluator, SuggestedFix, Verdict
from eval_agent.ingest import authority_results, marc_extract

_EDITABLE_TARGETS = frozenset({
    "matched_name", "entity_text", "mazal_id", "viaf_id", "wikidata_qid", "role",
})


class AuthorityEvaluator(Evaluator):
    id = "authority"
    sub_types = ["person", "organization", "place", "work"]
    rubric_name = "authority.md"
    marc_field_keys = [
        "title", "authors", "contributors", "subjects", "provenance",
        "notes", "dates", "place", "related_places", "colophon_text",
    ]

    def extract_candidates(
        self,
        *,
        ner_record: dict[str, Any],
        marc_record: dict[str, Any],
        threshold: float,
    ) -> Iterable[Candidate]:
        # ``ner_record`` is the authority-enriched record (carries
        # marc_authority_matches). ``marc_record`` is the Stage-1 MARC
        # slice for prompt context (falls back to the same record).
        #
        # The candidate set mirrors the curator's Authority editor: every
        # authority decision the pipeline made across all three shapes —
        # MARC-field matches, enriched NER entities, and KIMA places. The
        # ``threshold`` does NOT gate authority candidates: the whole point
        # of AI verification is to review the *uncertain* (medium/low)
        # matches, so gating to high-confidence only would defeat it. We
        # skip exactly one class: rows that never resolved to an authority
        # id (nothing to verify).
        rid = str(ner_record.get("_control_number", ""))
        ctx = marc_extract.project(marc_record or ner_record, self.marc_field_keys)

        matches: list[dict[str, Any]] = []
        matches.extend(
            m
            for m in authority_results.get_matches(ner_record)
            if authority_results.has_authority_id(m)
        )
        matches.extend(authority_results.get_enriched_entities(ner_record))
        matches.extend(authority_results.places_as_matches(ner_record))

        for idx, match in enumerate(matches):
            conf = authority_results.get_confidence(match)
            sub_type = _classify(match)
            payload = dict(match)
            payload["_match_index"] = idx
            yield Candidate(
                record_id=rid,
                evaluator_id=self.id,
                sub_type=sub_type,
                payload=payload,
                confidence=conf,
                marc_context=ctx,
            )

    def build_prompt(self, candidate: Candidate) -> str:
        p = candidate.payload
        ids = []
        if p.get("mazal_id"):
            ids.append(f"Mazal {p['mazal_id']}")
        if p.get("viaf_uri"):
            ids.append(f"VIAF {p['viaf_uri']}")
        if p.get("wikidata_qid"):
            ids.append(f"Wikidata {p['wikidata_qid']}")
        ids_str = " · ".join(ids) if ids else "(no authority id — unmatched)"
        sources = p.get("sources") or ([p["source"]] if p.get("source") else [])
        years = ""
        if p.get("birth_year") or p.get("death_year"):
            years = f"  years:       {p.get('birth_year', '?')}–{p.get('death_year', '?')}\n"
        guards = ""
        if p.get("guard_flags"):
            guards = f"  guards fired:{', '.join(p['guard_flags'])}\n"
        if p.get("rejection_reason"):
            guards += f"  rejection:   {p['rejection_reason']}\n"
        block = (
            f"Model: Authority resolution (Mazal / VIAF / Wikidata / KIMA)\n"
            f"Prediction:\n"
            f"  entity name: {p.get('name', '')}\n"
            f"  role/type:   {p.get('role', '')} ({candidate.sub_type})\n"
            f"  MARC field:  {p.get('field', '')}\n"
            f"  matched to:  {p.get('matched_name') or p.get('name', '')}\n"
            f"  authority:   {ids_str}\n"
            f"  sources:     {', '.join(str(s) for s in sources) or '(none)'}\n"
            f"{years}{guards}"
            f"  confidence:  {candidate.confidence:.3f}\n"
        )
        return self.render_prompt(candidate, prediction_block=block)

    def parse_verdict(self, raw: dict[str, Any] | None, candidate: Candidate) -> Verdict:
        v = super().parse_verdict(raw, candidate)
        if raw is None:
            return v
        fix = self._parse_authority_suggested_fix(raw.get("suggested_fix"), candidate)
        if fix is None:
            return v
        v.suggested_fix = fix
        payload_fix = {
            "target":       fix.source_field,
            "value":        fix.text,
            "text":         fix.text,
            "source_field": fix.source_field,
            "reasoning":    fix.reasoning,
            "confidence":   "high",
        }
        candidate.payload["suggested_fix"] = payload_fix
        v.candidate_payload["suggested_fix"] = payload_fix
        return v

    def _parse_authority_suggested_fix(
        self,
        raw_fix: Any,
        candidate: Candidate,
    ) -> SuggestedFix | None:
        if not isinstance(raw_fix, dict):
            return None
        if raw_fix.get("confidence") != "high":
            return None
        if not candidate.marc_context:
            return None

        target = str(raw_fix.get("source_field") or raw_fix.get("target") or "").strip()
        value = str(raw_fix.get("text") or raw_fix.get("value") or "").strip()
        if not target or target not in _EDITABLE_TARGETS or not value:
            return None
        if len(value) > 512:
            return None

        p = candidate.payload
        if target == "entity_text":
            current = str(p.get("entity_text") or p.get("name") or "").strip()
        elif target == "matched_name":
            current = str(p.get("matched_name") or "").strip()
        elif target == "viaf_id":
            current = str(p.get("viaf_id") or "").strip()
            if not current and p.get("viaf_uri"):
                current = str(p.get("viaf_uri")).rstrip("/").split("/")[-1].strip()
        else:
            current = str(p.get(target) or "").strip()
        if value == current:
            return None

        return SuggestedFix(
            text=value,
            reasoning=str(raw_fix.get("reasoning") or "") or None,
            source_field=target,
            confidence="high",
        )


def _classify(match: dict[str, Any]) -> str:
    """Map a match to a coarse sub_type used for metrics grouping."""
    mt = str(match.get("match_type") or match.get("_entity_kind") or "").lower()
    if "place" in mt or match.get("field") in {"651", "752"}:
        return "place"
    if "org" in mt or "corporate" in mt:
        return "organization"
    if "work" in mt:
        return "work"
    return "person"
