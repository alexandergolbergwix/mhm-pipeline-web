# Foreign-block resolution (F1–F4)

> Up: [Wikidata Studio](README.md) · Plan:
> [docs/plans/publication-foreign-block-resolution.md](../../../../docs/plans/publication-foreign-block-resolution.md)

Release `f0992dbf` (run `3494ebf5`) planned 107 `block (present_foreign)`
actions, all with the same detail ("Foreign QID consent does not match…").
16 manuscripts were project data created by a former project member, 54 works
and ~33 persons were genuine scholarly matches, ~4 persons were false string
matches. F1–F4 give every blocked item an executed path.

## F1 — P3959 identity ownership override

`WikidataUploader._is_our_item(qid, *, our_p3959=None)` accepts a second
ownership channel: when the live target carries the record's **exact** P3959
(NNL catalog control number), the item is project-owned data regardless of who
created it. `_assert_modifiable` forwards `our_p3959`; `upload_item`,
`_build_wbi_item`, and the publication gateway `_write_once_sync` pass it for
`entity_type == "manuscript"` only (persons/works keep the strict creator
check). Guards: never on test.wikidata.org, never when the remote item lacks
P3959, no prefix/fuzzy compare (`_remote_p3959_values` caches the fetched
claims per QID). `_write_once_sync` also converts a
`UnauthorisedModificationError` into an owned update once
`_p3959_identity_accepts` re-verifies the equality (log
`P3959_IDENTITY_ACCEPTED qid=…`).

The publication `_reconcile_sync` ownership split passes `our_p3959`, so a
P3959-equal manuscript observes `present_owned` and plans
`update (present_owned)`.

## F2 — Plan actions carry the reconcile method

`TargetObservation.method` / `PlanActionRecord.reconcile_method` round-trip the
`ReconcileOutcome.method` (`p3959/shelfmark`, `identifier`, `label+author`,
…; `reference-only` for pinned targets). Migration
`0051_plan_reconcile_method` adds one nullable
`publication_plan_actions.reconcile_method` column (NULL = legacy). The plan
digest includes the method; the run+target observation reuse cache carries it.

## F3 — Sweep unexecuted blocks out of the release

Before assessment, `wikidata_publication_auto_job` sweeps blocked entities
that have no `existing_qid` on their document (`route_blocked_target` in
`automatic_policy.py`):

1. exact P3959 identity (manuscript) → `update_existing`; the entity document
   gets `existing_qid` stamped (F1 write path in the same release);
2. shared strong identifier (P214/P8189/P244/P227/P213/P268, P3959) → routed
   to the AI dual judge (unchanged requirement);
3. nothing verifiable shared → **defer** with "A live-wiki item exists but
   shares no verifiable identifier; curator review required."

The sweep never accepts a community item implicitly. Progress:
"Sweeping N blocked items: M re-routed, K deferred".

## F4 — Drawer accept for foreign updates

Every blocked row renders `WikidataBlockedActionDrawer` (even without a stamped
`existing_qid`) with the remote item from `target_qid`/`target_revision`, a
claims diff (`our_statements` on `BlockedPlanAction` vs live claims fetched
with `origin=*`), and three choices: **Reuse without updates**
(reference-only prepare), **Accept & update** (writes the sealed consent —
qid + remote_revision + entity_digest; each click re-plans), **Leave it out of
this release**. The batch consent checkbox flow stays for multi-item consent.
The summary line shows `reused without updates: N · accepted foreign updates: M`
(`PlanSummary.accepted_foreign_update_count`).

Guard (W-221): only the curator's sealed consent writes onto a community item;
the F1 manuscript class bypasses it as project data.
