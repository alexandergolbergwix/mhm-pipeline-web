"""AI autofix evaluator for Wikidata Studio items with an existing QID.

Consumes the pre-baked ``wikidata_live`` compare snapshot attached by the
web backend before the eval-agent subprocess starts. Proposes structured
``suggested_fixes`` the curator can apply in one click.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from eval_agent.evaluators._base import Candidate, Evaluator, Verdict
from eval_agent.evaluators.wikidata_item import WikidataItemEvaluator


class WikidataAutofixEvaluator(WikidataItemEvaluator):
    id = "wikidata_autofix"
    rubric_name = "wikidata_autofix.md"

    def extract_candidates(
        self,
        *,
        ner_record: dict[str, Any],
        marc_record: dict[str, Any],
        threshold: float,
    ) -> Iterable[Candidate]:
        qid = ner_record.get("existing_qid")
        live = ner_record.get("wikidata_live")
        if not qid or not isinstance(live, dict) or live.get("error"):
            return
        for cand in super().extract_candidates(
            ner_record=ner_record,
            marc_record=marc_record,
            threshold=threshold,
        ):
            cand.payload["wikidata_live"] = live
            yield cand

    def build_prompt(self, candidate: Candidate) -> str:
        p = candidate.payload
        live = p.get("wikidata_live") or {}
        rows = live.get("rows") or []
        conflict_rows = [
            r for r in rows
            if isinstance(r, dict) and r.get("status") in ("conflict", "wikidata_only", "studio_only")
        ]
        compare_block = json.dumps(
            {
                "qid": live.get("qid"),
                "wikidata_labels": live.get("labels") or {},
                "wikidata_descriptions": live.get("descriptions") or {},
                "diff_rows": conflict_rows[:40],
                "conflict_count": live.get("conflict_count", 0),
            },
            ensure_ascii=False,
            indent=2,
        )
        base = super().build_prompt(candidate)
        return (
            f"{base}\n"
            f"Live Wikidata compare (pre-fetched):\n{compare_block}\n"
        )

    def parse_verdict(self, raw: dict[str, Any] | None, candidate: Candidate) -> Verdict:
        v = super().parse_verdict(raw, candidate)
        if raw is None:
            return v
        fixes = raw.get("suggested_fixes")
        if not isinstance(fixes, list):
            return v
        cleaned = [
            f for f in fixes
            if isinstance(f, dict) and str(f.get("confidence") or "") == "high"
        ]
        if not cleaned:
            return v
        candidate.payload["suggested_fixes"] = cleaned
        v.candidate_payload["suggested_fixes"] = cleaned
        first = cleaned[0]
        target = str(first.get("target") or "")
        value = first.get("value")
        if target.startswith("label.") and value:
            raw_fix = {
                "target": target,
                "value": str(value),
                "confidence": "high",
                "reasoning": first.get("reasoning"),
            }
            candidate.payload["suggested_fix"] = raw_fix
            v.candidate_payload["suggested_fix"] = raw_fix
        return v
