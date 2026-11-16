# Foreground reference correction

> Up: [Wikidata Studio](README.md) · W-343 · R193

An explicit user request can authorize one reviewed reference correction while the user waits.
The [MediaWiki maxlag manual](https://www.mediawiki.org/wiki/Manual:Maxlag_parameter) permits interactive requests to omit maxlag.
This mode does not convert the automatic repair queue to an interactive queue.
The automatic transport and retry runner retain `maxlag=5`.

## Scope

`ForegroundReferenceGateway` extends the normal live repair gateway.
The caller supplies a complete plan with `execution_mode="foreground_reference"`.
The plan has one authorized QID, one proposal, and one reference change.
The caller also supplies the explicitly confirmed QID and proposal digest.
The constructor validates the proposal against the complete before snapshot.
Only exact duplicate references or equivalent retrieval-date variants qualify.
The title, value, rank, qualifiers, and other statement fields cannot change.

The gateway seals the plan digest, QID, revision, and exact claim payload.
Its confirmation expires after five minutes of monotonic time.
Its lock permits one write attempt, including concurrent calls.
A rejection, timeout, or lost response does not restore that attempt.
The foreground parameter method removes only maxlag from the normal request.
It retains the same endpoint, authentication, CSRF token, baserevid, and transport timeout.
The HTTP adapter must have zero write retries.

## Durable execution

Call `advance_repair_publication` once with the foreground gateway and the same confirmed plan.
The helper selects the separate `gila-foreground-reference` profile.
A normal gateway cannot execute a foreground plan.
A foreground gateway cannot execute a normal or changed plan.
`wait_for_repair_publication` rejects foreground mode before any Publication advance.
There is no job, schedule, automatic fallback, new route, migration, or environment variable.

The trusted caller must already have current project access and a live actor credential.
Use the normal credential resolver; do not use copied browser cookies.
The normal gateway checks the authenticated account and creator user ID `6935067`.
It also checks the moratorium, bot exclusion, current source, exact claim, and revision.
Foreign-created entities remain read-only targets.
The Publication module commits its full intent before the HTTP write.
Success requires the complete public entity to match the intended result and response revision.
An uncertain outcome retains the normal recovery path and cannot authorize a blind resend.

The mode does not alter the six blocked Studio source corrections.
Those corrections still require their normal authorized source-review path.

## Caller shape

```python
gateway = ForegroundReferenceGateway(
    uploader, plan=plan, confirmed_qid=reviewed_qid,
    confirmed_proposal_sha256=entity_digest(plan["proposals"][0]),
)
summary = await advance_repair_publication(
    plan, gateway, source_check, repository,
    actor_id=actor_id, run_id=run_id,
)
```

The caller must not place this block inside a batch or retry loop.
A later correction requires another explicit user request and a new current proposal.
Tests: `backend/tests/unit/test_wikidata_foreground_repair.py`.
