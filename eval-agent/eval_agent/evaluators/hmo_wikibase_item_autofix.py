"""AI autofix evaluator for HMO Wikibase items with a live QID."""

from __future__ import annotations

import json
from typing import Any, Iterable

from eval_agent.evaluators._base import Verdict
from eval_agent.evaluators.hmo_wikibase_item import HmoWikibaseItemEvaluator


class HmoWikibaseItemAutofixEvaluator(HmoWikibaseItemEvaluator):
    id = "hmo_wikibase_item_autofix"
    rubric_name = "hmo_wikibase_item_autofix.md"

    def extract_candidates(
        self,
        *,
        ner_record: dict[str, Any],
        marc_record: dict[str, Any],
        threshold: float,
    ) -> Iterable:
        qid = ner_record.get("wikibase_id")
        live = ner_record.get("wikibase_live")
        if not qid or not isinstance(live, dict) or live.get("error"):
            return
        for cand in super().extract_candidates(
            ner_record=ner_record,
            marc_record=marc_record,
            threshold=threshold,
        ):
            cand.payload["wikibase_live"] = live
            yield cand

    def build_prompt(self, candidate) -> str:
        p = candidate.payload
        live = p.get("wikibase_live") or {}
        rows = live.get("rows") or []
        conflict_rows = [
            r for r in rows
            if isinstance(r, dict) and r.get("status") in ("conflict", "wikidata_only", "studio_only")
        ]
        compare_block = json.dumps(
            {
                "qid": live.get("qid"),
                "wikibase_labels": live.get("labels") or {},
                "wikibase_descriptions": live.get("descriptions") or {},
                "diff_rows": conflict_rows[:40],
                "conflict_count": live.get("conflict_count", 0),
            },
            ensure_ascii=False,
            indent=2,
        )
        return f"{super().build_prompt(candidate)}\nLive Wikibase compare:\n{compare_block}\n"

    def parse_verdict(self, raw: dict[str, Any] | None, candidate) -> Verdict:
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
        return v
