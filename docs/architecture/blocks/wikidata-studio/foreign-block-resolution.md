# Foreign-block resolution (F1–F4)

> Up: [Wikidata Studio](README.md) · Plan:
> [docs/plans/publication-foreign-block-resolution.md](../../../../docs/plans/publication-foreign-block-resolution.md)

Release `f0992dbf` (run `3494ebf5`) planned 107 `block (present_foreign)`
actions, all with the same detail ("Foreign QID consent does not match…").
16 manuscripts were project data created by a former project member, 54 works
and ~33 persons were genuine scholarly matches, ~4 persons were false string
matches. F1–F4 give every blocked item an executed path.

## F1 — P3959 identifies, but does not authorize

W-302 supersedes the former P3959 ownership override. An exact P3959 match
supports reconciliation but does not prove creator ownership or permit an
update. `_is_our_item` checks the authenticated creator; a foreign target
requires explicit approval for its QID. Publication classifies a matching
foreign-created manuscript as `present_foreign`.

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

1. shared strong identifier (P214/P8189/P244/P227/P213/P268, P3959) → routed
   to the AI dual judge (unchanged requirement);
2. nothing verifiable shared → **defer** with "A live-wiki item exists but
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

Guard (W-221/W-302): only the curator's sealed consent authorizes a foreign
Publication update. P3959 does not bypass this guard.
