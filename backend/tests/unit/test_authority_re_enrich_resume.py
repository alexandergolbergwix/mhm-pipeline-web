"""Per-entity enrichment skip (Rule W-241/R33).

A re-run must not re-match entities whose AuthorityMatch row was already
enriched after the last upstream change — that is what made every
"Build items" attempt restart the 5.3k-entity pass from 0/5295.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.run import AuthorityMatch, Run, RunRecord
from app.pipeline.authority import Candidate
from app.pipeline.authority_re_enrich import re_enrich_run


class _StubMatcher:
    """Returns one deterministic candidate per call; counts invocations."""

    def __init__(self) -> None:
        self.calls = 0

    async def match(self, entity, marc_record, *, db_session=None, user_id=None, skip_cache=False):
        self.calls += 1
        return [
            Candidate(
                matched_name=f"Matched {entity.get('text')}",
                confidence="high",
                source="test",
                mazal_id="M1",
                wikidata_qid="Q42",
                payload={"k": "v"},
            )
        ]


async def _records(db_session, run_id) -> list[RunRecord]:
    return list(
        (await db_session.execute(
            select(RunRecord).where(RunRecord.run_id == run_id)
        )).scalars().all()
    )


async def _matches(db_session, run_id) -> list[AuthorityMatch]:
    return list(
        (await db_session.execute(
            select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
        )).scalars().all()
    )


@pytest.mark.asyncio
async def test_second_pass_skips_fresh_enriched_entities(
    sample_run, db_session, monkeypatch,
) -> None:
    db_session.add(RunRecord(
        run_id=sample_run["run_id"],
        control_number="990001800310205171",
        marc={"_control_number": "990001800310205171", "title": "t"},
    ))
    await db_session.commit()

    run = (
        await db_session.execute(select(Run).where(Run.id == sample_run["run_id"]))
    ).scalars().one()

    matcher = _StubMatcher()

    async def progress(_p: int, _t: int, _m: str) -> None:
        return None

    stats1 = await re_enrich_run(
        db_session, run, matcher,
        skip_cache=False, skip_fresh_enriched=False,
        records=await _records(db_session, sample_run["run_id"]),
        existing_rows=[],
        on_progress=progress,
    )
    assert stats1["newly_matched"] >= 1
    calls_after_pass1 = matcher.calls

    stats2 = await re_enrich_run(
        db_session, run, matcher,
        skip_cache=False, skip_fresh_enriched=True,
        records=await _records(db_session, sample_run["run_id"]),
        existing_rows=await _matches(db_session, sample_run["run_id"]),
        on_progress=progress,
    )
    assert matcher.calls == calls_after_pass1, (
        "fresh entities must be skipped without a matcher call"
    )
    assert stats2["skipped_fresh"] >= 1
    assert stats2["content_changed"] is False


@pytest.mark.asyncio
async def test_upstream_change_rematches_even_when_fresh(
    sample_run, db_session, monkeypatch,
) -> None:
    db_session.add(RunRecord(
        run_id=sample_run["run_id"],
        control_number="990001800310205171",
        marc={"_control_number": "990001800310205171", "title": "t"},
    ))
    await db_session.commit()

    run = (
        await db_session.execute(select(Run).where(Run.id == sample_run["run_id"]))
    ).scalars().one()

    matcher = _StubMatcher()

    async def progress(_p: int, _t: int, _m: str) -> None:
        return None

    await re_enrich_run(
        db_session, run, matcher,
        skip_cache=False, skip_fresh_enriched=False,
        records=await _records(db_session, sample_run["run_id"]),
        existing_rows=[],
        on_progress=progress,
    )
    calls_after_pass1 = matcher.calls

    # A NER row updated AFTER the enrichment → fresh_cutoff moves past it.
    from app.models.extraction_approval import ExtractionApproval

    db_session.add(ExtractionApproval(
        run_id=sample_run["run_id"],
        control_number="990001800310205171",
        text="משה בן מימון",
        type="PERSON",
        source="person_ner",
        start=0,
        end=10,
        updated_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        + timedelta(minutes=5),
    ))
    await db_session.commit()

    stats2 = await re_enrich_run(
        db_session, run, matcher,
        skip_cache=False, skip_fresh_enriched=True,
        records=await _records(db_session, sample_run["run_id"]),
        existing_rows=await _matches(db_session, sample_run["run_id"]),
        on_progress=progress,
    )
    assert matcher.calls > calls_after_pass1, (
        "an upstream NER change must invalidate the fresh-skip"
    )
    assert stats2["skipped_fresh"] == 0
