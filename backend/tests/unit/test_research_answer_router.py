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
