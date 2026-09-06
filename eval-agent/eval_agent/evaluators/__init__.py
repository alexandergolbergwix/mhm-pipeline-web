"""Registry of all known evaluators.

Each entry maps the evaluator's canonical ``id`` to its class. Adding
a new evaluator is one line here + one module under this directory.
"""

from eval_agent.evaluators._base import Candidate, Evaluator, Verdict
from eval_agent.evaluators.authority import AuthorityEvaluator
from eval_agent.evaluators.contents_ner import ContentsNERevaluator
from eval_agent.evaluators.genre_classifier import GenreClassifierEvaluator
from eval_agent.evaluators.hmo_wikibase_item import HmoWikibaseItemEvaluator
from eval_agent.evaluators.hmo_wikibase_item_autofix import HmoWikibaseItemAutofixEvaluator
from eval_agent.evaluators.hmo_wikibase_schema import HmoWikibaseSchemaEvaluator
from eval_agent.evaluators.person_ner import PersonNERevaluator
from eval_agent.evaluators.provenance_ner import ProvenanceNERevaluator
from eval_agent.evaluators.wikidata_autofix import WikidataAutofixEvaluator
from eval_agent.evaluators.wikidata_publication_review import WikidataPublicationReviewEvaluator
from eval_agent.evaluators.wikidata_item import WikidataItemEvaluator
from eval_agent.evaluators.wikidata_test_live_ready import WikidataTestLiveReadyEvaluator

# ``marc500_colophon`` was removed 2026-05-23 after the conf>=0.90
# eval-agent run showed 76% of its high-confidence predictions failing
# (6% strict / 16% lenient precision overall). The classifier head
# was too noisy to ship; weights are kept on disk locally for future
# revisits but are no longer bundled, surfaced, or evaluated.

REGISTRY: dict[str, type[Evaluator]] = {
    "person_ner": PersonNERevaluator,
    "provenance_ner": ProvenanceNERevaluator,
    "contents_ner": ContentsNERevaluator,
    "genre_classifier": GenreClassifierEvaluator,
    # Stage-3 authority resolution (Mazal / VIAF / Wikidata / KIMA).
    # Reads authority_enriched.json, not ner_results.json.
    "authority": AuthorityEvaluator,
    # Wikidata Studio item projection. Reads wikidata_items.json.
    "wikidata_item": WikidataItemEvaluator,
    "wikidata_publication_review": WikidataPublicationReviewEvaluator,
    "wikidata_autofix": WikidataAutofixEvaluator,
    "wikidata_test_live_ready": WikidataTestLiveReadyEvaluator,
    # HMO Wikibase Studio schema bootstrap. Reads hmo_wikibase_schema.json.
    "hmo_wikibase_schema": HmoWikibaseSchemaEvaluator,
    "hmo_wikibase_item": HmoWikibaseItemEvaluator,
    "hmo_wikibase_item_autofix": HmoWikibaseItemAutofixEvaluator,
}

# Evaluators that read authority_enriched.json instead of ner_results.json.
AUTHORITY_EVALUATORS: frozenset[str] = frozenset({"authority"})

# Evaluators that read wikidata_items.json instead of ner_results.json.
WIKIDATA_ITEM_EVALUATORS: frozenset[str] = frozenset({
    "wikidata_item", "wikidata_autofix", "wikidata_test_live_ready", "wikidata_publication_review",
})

# Evaluators that read hmo_wikibase_schema.json instead of ner_results.json.
HMO_WIKIBASE_SCHEMA_EVALUATORS: frozenset[str] = frozenset({"hmo_wikibase_schema"})

HMO_WIKIBASE_ITEM_EVALUATORS: frozenset[str] = frozenset({
    "hmo_wikibase_item", "hmo_wikibase_item_autofix",
})


def build(evaluator_id: str) -> Evaluator:
    cls = REGISTRY.get(evaluator_id)
    if cls is None:
        raise KeyError(
            f"unknown evaluator: {evaluator_id!r} "
            f"(known: {sorted(REGISTRY)})"
        )
    return cls()


def all_evaluators() -> list[Evaluator]:
    return [cls() for cls in REGISTRY.values()]


__all__ = [
    "Candidate", "Evaluator", "Verdict", "REGISTRY", "build", "all_evaluators",
]
