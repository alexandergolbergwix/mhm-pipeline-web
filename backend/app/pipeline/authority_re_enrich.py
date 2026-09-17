"""Shared authority re-enrich orchestration for POST and SSE endpoints."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import AuthorityMatch, Run, RunRecord
from app.pipeline.entity_normalize import (
    normalize_entity_key,
    normalize_entity_text,
    normalize_role,
)
from app.pipeline.marc_ingest import extract_named_entities, prepare_record_for_pipeline

logger = logging.getLogger(__name__)

ProgressCb = Callable[[int, int, str], Awaitable[None]]


def match_key(
    control_number: str,
    text: str,
    kind: str,
    role: str,
) -> tuple[str, str, str, str]:
    return (
        control_number,
        normalize_entity_key(normalize_entity_text(text)),
        kind,
        normalize_role(role),
    )


class ReEnrichCancelled(Exception):
    """Cancel flag observed mid-enrichment.

    The caller (``execute_hmo_item_build``) maps this to the job runner's
    ``cancelled`` path. Partial work stays committed (per-entity commits
    + ``enriched_at`` skip-fresh make the next run resume instantly).
    """


async def re_enrich_run(
    db: AsyncSession,
    run: Run,
    matcher: Any,
    *,
    skip_cache: bool,
    skip_fresh_enriched: bool = False,
    records: list[RunRecord],
    existing_rows: list[AuthorityMatch],
    on_progress: ProgressCb | None = None,
    should_cancel: Callable[[], Awaitable[bool]] | None = None,
) -> dict[str, int]:
    """Re-match every entity; upsert by normalised key; purge orphan rows.

    ``on_progress(processed, total, message)`` is throttled (~1s) and reports
    per-entity work so long HMO rebuilds can show a sub-progress bar.
    ``should_cancel`` is polled on every progress emit — the sweep and the
    concurrent gather can otherwise run for an hour before the runner's
    next phase-boundary cancel check (2026-09-17: Cancel stayed unresponsive
    for 20+ min while the sweep crawled).
    """
    run_id = run.id
    user_id = run.created_by

    existing_idx: dict[tuple[str, str, str, str], list[AuthorityMatch]] = defaultdict(list)
    orphan_pairs: list[tuple[AuthorityMatch, tuple[str, str, str, str]]] = []
    for m in existing_rows:
        key = match_key(
            str(m.control_number or ""),
            str(m.entity_text or ""),
            str(m.entity_kind or ""),
            str(m.role or ""),
        )
        existing_idx[key].append(m)
        orphan_pairs.append((m, key))
        # Rows created before the enriched_at feature (or by the finalize
        # cross-link pass) carry no stamp — treat their creation as their
        # enrichment so a re-run can skip them like any fresh row.
        if m.enriched_at is None:
            m.enriched_at = m.created_at

    # Materialise records before any await — async ORM cannot lazy-load after
    # matcher threads / flush expire attributes on the shared session.
    record_rows: list[tuple[str, dict[str, Any]]] = []
    for rec in records:
        record_rows.append((
            str(rec.control_number or ""),
            prepare_record_for_pipeline(dict(rec.marc or {})),
        ))

    work: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for control_number, marc in record_rows:
        for entity in extract_named_entities(marc):
            work.append((control_number, marc, entity))

    produced_keys: set[tuple[str, str, str, str]] = set()
    checked = 0
    updated = 0
    newly_matched = 0
    orphans_removed = 0
    skipped_fresh = 0
    # True when re-enrichment materially changed any AuthorityMatch row —
    # the HMO item build rebuilds RDF + items only when this is set, so a
    # no-op refresh can hit the item fingerprint cache instead of burning
    # a full rebuild.
    content_changed = False
    total = len(work)
    last_emit = 0.0

    # When not forcing fresh lookups, entities whose row was enriched after
    # the last upstream input change are skipped entirely — a restarted or
    # repeated pass visits them instantly instead of re-matching (R33).
    # No NER rows → no upstream constraint → every enriched row is fresh.
    fresh_cutoff: datetime | None = None
    if not skip_cache:
        from app.models.extraction_approval import ExtractionApproval  # noqa: PLC0415

        ner_latest = await db.scalar(
            select(func.max(ExtractionApproval.updated_at)).where(
                ExtractionApproval.run_id == run_id,
            )
        )
        fresh_cutoff = ner_latest or datetime(1970, 1, 1, tzinfo=timezone.utc)

    async def _maybe_progress(processed: int, control_number: str, text: str) -> None:
        nonlocal last_emit
        if on_progress is None or total <= 0:
            return
        now = time.monotonic()
        if processed not in (1, total) and (now - last_emit) < 1.0:
            return
        label = (text or "").strip()
        if len(label) > 48:
            label = label[:45] + "…"
        message = f"{control_number}: {label}" if label else control_number
        await on_progress(processed, total, message)
        # Reset AFTER the write completes. A slow emit (Modal→Postgres
        # round trips exceed the 1 s window) must not pace the sweep at
        # emit latency: with last_emit set before the await, the next
        # iteration always passes the throttle and every entity pays the
        # full write cost (2026-09-17: the sweep crawled at ~1 entity/s
        # from the Modal container).
        last_emit = time.monotonic()

    async def _emit_phase(processed: int, message: str) -> None:
        if on_progress is None or total <= 0:
            return
        await on_progress(min(processed, total), total, message)

    # Cancel polling is time-throttled (~1/s) and independent of
    # on_progress: the runner's phase-boundary checks alone left Cancel
    # unresponsive for the whole multi-minute sweep/gather (2026-09-17).
    last_cancel_check = 0.0

    async def _check_cancel() -> None:
        nonlocal last_cancel_check
        if should_cancel is None:
            return
        now = time.monotonic()
        if now - last_cancel_check < 1.0:
            return
        last_cancel_check = now
        if await should_cancel():
            raise ReEnrichCancelled()

    async def _emit_phase(processed: int, message: str) -> None:
        if on_progress is None or total <= 0:
            return
        await on_progress(min(processed, total), total, message)

    # ── Phase A — concurrent matching (R34) ───────────────────────────
    # matcher.match is network-bound (VIAF HTTP, KIMA/Mazal lookups);
    # running 5k+ of them serially costs hours. Entities that need a
    # match are dispatched with bounded concurrency, each with its OWN
    # short DB session (the inference cache commits internally). The
    # serial DB-apply phase below then never holds a transaction across
    # network work (Rule W-240).
    concurrency = int(os.getenv("ENRICH_CONCURRENCY", "8"))
    sem = asyncio.Semaphore(max(1, concurrency))

    pending: list[tuple[str, dict, dict, str, str, str]] = []
    for control_number, marc, entity in work:
        checked += 1
        await _check_cancel()
        clean_text = normalize_entity_text(entity.get("text", ""))
        clean_role = normalize_role(entity.get("role", ""))
        kind = entity.get("kind", "person")
        key = match_key(control_number, clean_text, kind, clean_role)
        produced_keys.add(key)

        # Skip entities whose enrichment is still fresh (R33): nothing
        # upstream changed since their last match, so re-matching would
        # return the same answer at network-lookup cost. Their row gets
        # its produced_keys entry right above (orphan purge still sees it).
        matches = existing_idx.get(key, [])
        primary = matches[0] if matches else None
        if (
            fresh_cutoff is not None
            and primary is not None
            and primary.enriched_at is not None
            and primary.enriched_at >= fresh_cutoff
        ):
            skipped_fresh += 1
            await _maybe_progress(checked, control_number, clean_text)
            continue

        pending.append((key, control_number, marc, entity, clean_text, clean_role, kind))
        await _maybe_progress(checked, control_number, clean_text)

    # ── Release the main session's transaction before the gather ──────
    # Phase A only reads; commit so the connection never sits
    # idle-in-transaction during the possibly hour-long concurrent
    # matching phase. Postgres kills an idle-in-transaction connection
    # after 120s and the first Phase B commit then dies with "the
    # underlying connection is closed" (2026-09-17 incident, Rule
    # W-240). This also persists the enriched_at stamps for pre-feature
    # rows. expire_on_commit=False keeps the loaded rows usable for
    # Phase B's per-entity apply; the session stays attached, so do NOT
    # close() it here (Phase B mutates/deletes these in-memory rows).
    await db.commit()

    if pending:
        await _emit_phase(
            len(work) - len(pending),
            f"Replaying {skipped_fresh} fresh entities; matching {len(pending)} pending…",
        )

    async def _match_one(
        item: tuple[str, dict, dict, str, str, str],
    ) -> tuple[str, list]:
        key, control_number, marc, entity, _ct, _cr, _k = item
        async with sem:
            from app.db import session_scope as _session_scope

            try:
                async with _session_scope() as task_db:
                    candidates = await matcher.match(
                        entity, marc,
                        db_session=task_db,
                        user_id=user_id,
                        skip_cache=skip_cache,
                    )
                return key, candidates
            except Exception:  # noqa: BLE001
                logger.exception(
                    "re-enrich: authority match failed for %r", entity.get("text"),
                )
                return key, []

    match_results: dict[str, list] = {}
    if pending:
        # Per-completion progress (W-113): the sweep reaches the full bar
        # in seconds, then the concurrent match used to run with zero UI
        # feedback for the whole fan-out (2026-09-17: the build sat at
        # "5295 / 5295 entities" while matching ran). Report each
        # completed match so the bar climbs back to the total as results
        # land; the pending count is unique entity keys (R16).
        sweep_done = len(work) - len(pending)
        matched_done = 0
        last_match_emit = 0.0
        for coro in asyncio.as_completed([_match_one(item) for item in pending]):
            key, candidates = await coro
            match_results[key] = candidates
            matched_done += 1
            await _check_cancel()
            now = time.monotonic()
            if matched_done == len(pending) or now - last_match_emit >= 1.0:
                await _emit_phase(
                    sweep_done + matched_done,
                    f"Matching pending entities… {matched_done}/{len(pending)}",
                )
                # Same post-emit reset as _maybe_progress (see above).
                last_match_emit = time.monotonic()

    # ── Phase B — serial DB apply, short per-entity transactions ──────
    for key, control_number, marc, entity, clean_text, clean_role, kind in pending:
        candidates = match_results.get(key) or []

        if not candidates:
            # Commit per entity: the transaction must never stay open
            # across the next entity's network lookups — the engine sets
            # idle_in_transaction_session_timeout=120s and Postgres kills
            # the connection mid-pass otherwise (Rule W-240).
            await db.commit()
            continue

        c = candidates[0]

        if key in existing_idx:
            matches = existing_idx[key]
            primary = matches[0]
            for dup in matches[1:]:
                await db.delete(dup)
                orphans_removed += 1
                content_changed = True
            existing_idx[key] = [primary]
            # Only touch rows whose content actually changed (and only
            # count those as "updated") so a no-op refresh keeps the
            # downstream fingerprint caches valid.
            _CONTENT_FIELDS = (
                ("entity_text", clean_text),
                ("role", clean_role),
                ("entity_kind", kind),
                ("matched_name", c.matched_name),
                ("mazal_id", c.mazal_id),
                ("viaf_id", c.viaf_id),
                ("wikidata_qid", c.wikidata_qid),
                ("source", c.source),
                ("payload", c.payload),
            )
            row_changed = False
            for field, new_val in _CONTENT_FIELDS:
                if getattr(primary, field) != new_val:
                    setattr(primary, field, new_val)
                    row_changed = True
            new_approved = (
                kind == "place"
                and bool(c.wikidata_qid and c.mazal_id)
                and int((c.payload or {}).get("source_count") or 0) >= 2
            )
            if bool(primary.approved) != bool(new_approved):
                primary.approved = new_approved
                row_changed = True
            if row_changed:
                updated += 1
                content_changed = True
            # Confidence is a volatile model score that does not reach the
            # RDF graph — write it through but never count it as a change
            # (otherwise the item fingerprint cache would never hit).
            primary.confidence = c.confidence
            primary.enriched_at = datetime.now(timezone.utc)
        else:
            row = AuthorityMatch(
                run_id=run_id,
                control_number=control_number,
                entity_text=clean_text,
                entity_kind=kind,
                role=clean_role,
                matched_name=c.matched_name,
                mazal_id=c.mazal_id,
                viaf_id=c.viaf_id,
                wikidata_qid=c.wikidata_qid,
                confidence=c.confidence,
                source=c.source,
                payload=c.payload,
                approved=(kind == "place" and bool(c.wikidata_qid and c.mazal_id) and int((c.payload or {}).get("source_count") or 0) >= 2),
                enriched_at=datetime.now(timezone.utc),
            )
            db.add(row)
            await db.flush()
            existing_idx[key] = [row]
            newly_matched += 1

        # Commit per entity: the transaction must never stay open across
        # the next entity's network lookups — the engine sets
        # idle_in_transaction_session_timeout=120s and Postgres kills the
        # connection mid-pass otherwise (Rule W-240). Also makes a
        # preempted Modal restart cheap: committed matches stay committed.
        await db.commit()

    for m, key in orphan_pairs:
        if key not in produced_keys:
            await db.delete(m)
            orphans_removed += 1

    remaining_rows = (
        await db.execute(
            select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
        )
    ).scalars().all()
    from app.pipeline.authority_post_enrich import finalize_authority_matches  # noqa: PLC0415

    # Finalize hardens all remaining rows synchronously (blocking loop) and
    # dirties a huge JSON payload per row — run it in chunks with a commit
    # between each so neither the open transaction nor the flush grows past
    # what one connection survives (Rule W-240).
    cross_linked = 0
    wd_crosschecked = 0
    _FINALIZE_CHUNK = 400
    rows_list = list(remaining_rows)
    finalize_done = 0
    for i in range(0, len(rows_list), _FINALIZE_CHUNK):
        chunk_stats = finalize_authority_matches(rows_list[i:i + _FINALIZE_CHUNK])
        cross_linked += chunk_stats["cross_linked"]
        wd_crosschecked += chunk_stats["wikidata_crosschecked"]
        await db.commit()
        finalize_done = min(finalize_done + _FINALIZE_CHUNK, len(rows_list))
        await _emit_phase(
            len(work),
            f"Hardening authority evidence… {finalize_done}/{len(rows_list)} rows",
        )

    remaining_count = await db.scalar(
        select(func.count())
        .select_from(AuthorityMatch)
        .where(AuthorityMatch.run_id == run_id)
    )
    run.match_count = int(remaining_count or 0)
    await db.commit()
    return {
        "checked": checked,
        "updated": updated,
        "newly_matched": newly_matched,
        "orphans_removed": orphans_removed,
        "cross_linked": cross_linked,
        "wikidata_crosschecked": wd_crosschecked,
        "skipped_fresh": skipped_fresh,
        # Any row mutation (or the finalize pass adding cross-links) means
        # the downstream RDF/TTL — and therefore the item cache — is stale.
        "content_changed": bool(
            content_changed or newly_matched or orphans_removed or cross_linked
        ),
    }
