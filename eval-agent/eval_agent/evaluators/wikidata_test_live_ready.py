"""Judge a test.wikidata.org landing against the Studio native for live.

Reads the same ``wikidata_items.json`` fixture as ``wikidata_item``. Extra
keys on each row (``test_wiki_snapshot``, ``deterministic_audit``,
``live_existing_snapshot``, ``upload_outcome``) are landing evidence only.
The native statements remain the live write payload — test Q/P ids must
never be recommended for www.wikidata.org.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Iterable

from eval_agent.evaluators._base import Candidate
from eval_agent.evaluators.wikidata_item import WikidataItemEvaluator


class WikidataTestLiveReadyEvaluator(WikidataItemEvaluator):
    id = "wikidata_test_live_ready"
    rubric_name = "wikidata_test_live_ready.md"

    def extract_candidates(
        self,
        *,
        ner_record: dict[str, Any],
        marc_record: dict[str, Any],
        threshold: float,
    ) -> Iterable[Candidate]:
        for cand in super().extract_candidates(
            ner_record=ner_record,
            marc_record=marc_record,
            threshold=threshold,
        ):
            payload = dict(cand.payload)
            payload["test_wiki_snapshot"] = ner_record.get("test_wiki_snapshot") or {}
            payload["deterministic_audit"] = ner_record.get("deterministic_audit") or {}
            payload["live_existing_snapshot"] = (
                ner_record.get("live_existing_snapshot") or {}
            )
            payload["upload_outcome"] = ner_record.get("upload_outcome") or {}
            yield replace(cand, payload=payload)

    def build_prompt(self, candidate: Candidate) -> str:
        base = super().build_prompt(candidate)
        p = candidate.payload
        extra = (
            "\n\nLIVE-READINESS PACK — test.wikidata.org is landing proof only. "
            "The Studio native above is what www.wikidata.org would receive. "
            "NEVER copy a test.wikidata.org Q-id or P-id onto live Wikidata "
            "(Rules W-182 / W-183). "
            "Test P/Q numbers WILL differ from live (remap). That is expected "
            "and is NOT a fail. `test_qid` in the audit is a test landing id, "
            "not a live QID — do not fail merely because it is present. "
            "In-batch `__LOCAL:` pointing at another Studio CREATE in this "
            "corpus is Rule W-192 pass 2, not a live fail. Fail only dangling "
            "`__LOCAL:` with no in-corpus target.\n"
            "  upload outcome:\n"
            f"{json.dumps(p.get('upload_outcome') or {}, ensure_ascii=False, indent=2)}\n"
            "  deterministic audit (hard blockers already computed; if "
            "blockers is non-empty you MUST set overall=fail):\n"
            f"{json.dumps(p.get('deterministic_audit') or {}, ensure_ascii=False, indent=2)}\n"
            "  test.wikidata.org snapshot (remap/landing evidence; ids are "
            "test-wiki ids, not live ids):\n"
            f"{json.dumps(p.get('test_wiki_snapshot') or {}, ensure_ascii=False, indent=2)}\n"
            "  live www.wikidata.org existing item (empty unless this native "
            "already has an existing_qid):\n"
            f"{json.dumps(p.get('live_existing_snapshot') or {}, ensure_ascii=False, indent=2)}\n"
        )
        marker = "\nReturn only the JSON verdict."
        if marker in base:
            return base.replace(marker, extra + marker, 1)
        return base + extra
