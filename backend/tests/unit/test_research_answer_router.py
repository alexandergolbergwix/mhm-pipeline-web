"""Deterministic-first answering: fact questions never need an LLM (P2)."""
from __future__ import annotations

import pytest

from app.services.research_agent.answer_router import deterministic_answer
from app.services.research_agent.model_fallback import is_quota_error  # noqa: F401


@pytest.mark.asyncio
async def test_wikidata_count_question_is_answered_from_the_db(db_session, sample_run):
    from app.services.research_agent.answer_router import deterministic_answer as det

    answer = await det(
        "how many entities weve added to wikidata",
        db_session,
        project_id=str(sample_run["project_id"]),
    )

    assert answer is not None
    assert answer.kind == "wikidata_counts"
    assert "Wikidata" in answer.text
    assert "no AI estimate" in answer.text


@pytest.mark.asyncio
async def test_off_topic_and_synthesis_questions_route_to_the_agent(db_session, sample_run):
    for question in (
        "Compare the provenance of the two Isaiah manuscripts",
        "Show the co-occurring works in the corpus",
        "",
    ):
        answer = await deterministic_answer(
            question, db_session, project_id=str(sample_run["project_id"]),
        )
        assert answer is None, question


@pytest.mark.asyncio
async def test_wikidata_count_answer_separates_items_from_writes_and_cites_sources(
    db_session, sample_run, monkeypatch,
):
    import app.services.research_agent.answer_router as answer_router

    async def _stats(_db, *, project_id):
        return {
            "write_ops": 5801, "distinct_items": 3628,
            "created_ops": 3409, "updated_ops": 2392,
            "run_id": "3494ebf5-ceb0-4702-a94e-c8f10be7f251",
            "publication_id": "f0992dbf-510e-4a61-906c-8b62622417ce",
        }

    monkeypatch.setattr(answer_router, "_wikidata_stats", _stats)

    answer = await answer_router.deterministic_answer(
        "how many entities weve added to wikidata",
        db_session,
        project_id=str(sample_run["project_id"]),
        web_base="https://example.test",
    )

    assert answer is not None
    # The two counts must be named distinctly — the 2026-10-18 conflation.
    assert "distinct items" in answer.text
    assert "3628" in answer.text
    assert "write operations" in answer.text
    assert "5801" in answer.text
    # Every number ships with a link to its real source.
    assert "Sources:" in answer.text
    assert "](" in answer.text
    assert "wikidata-studio" in answer.text
    assert all(e["url"].startswith("https://example.test") for e in answer.evidence)


def test_prompt_pins_the_live_verification_and_scope_rules():
    """The 2026-10-18 lesson (1,740 'manuscripts' for an 897-record corpus)
    must stay pinned: live-check numbers, name scope, respect the ceiling."""
    import re as _re

    from app.services.research_agent.prompt import SYSTEM_PROMPT

    flat = _re.sub(r"\s+", " ", SYSTEM_PROMPT).lower()
    for phrase in (
        "checked against the live source",
        "scope every number",
        "source-count ceiling",
        "never present a project-wide figure as a corpus figure",
        "reconcile before you report",
    ):
        assert phrase in flat, phrase
