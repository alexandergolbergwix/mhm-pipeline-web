# Gila repairs after access returns

Prepared on 17 November 2026. This file describes future work. It does not certify an applied correction.

## Objective and scope

Complete the six Studio corrections first. Then rebuild, publish supported corrections, and repeat the full Gila audit.
Keep the local source corrections separate from public Wikidata writes.
A local save does not prove a public correction.

Repository:

```text
/Users/alexandergo/Documents/Documents - IL-GQ5HP3MY57/Doctorat/mhm-pipeline-web
```

Run ID: `47e03437-09f2-4d6b-aea7-89fe79d76198`.

Studio: <https://mhm-pipeline.org/runs/47e03437-09f2-4d6b-aea7-89fe79d76198/wikidata-studio>.

## 1. Confirm that the access block is removed

- [ ] Obtain confirmation that the effective saved or managed restriction changed.
- [ ] Test the normal Studio access once.
- [ ] Confirm that the agent can access the application through its normal permission gate.
- [ ] Confirm the current application account and project access.

An open page on the user's screen does not prove agent access.
An `allow` entry in local configuration also does not prove effective access.
If the gate still denies access, stop the affected action.
Do not use another endpoint, browser surface, key, or Heroku command to perform the same blocked action.

After access returns, the operator can use the normal UI or the authorized backend workflow.
Any backend command must preserve the application's access checks, source validators, event history, and transaction rules.
This guide supplies no new special-key endpoint or tested six-item apply command.

## 2. Preserve the write boundary

- [ ] Require Wikidata account user ID `6935067`: `Alexander Goldberg IL`.
- [ ] Read each public target's first revision before a write.
- [ ] Require creator user ID `6935067` on each existing target.
- [ ] Treat missing, hidden, redirected, or conflicting creator proof as a refusal.
- [ ] Keep foreign-created items as validated link targets only.
- [ ] Preserve `maxlag=5` for automatic repairs.
- [ ] Stop on an unknown write outcome until normal recovery establishes its result.

The application actor also needs current owner or editor access to the project.
An application user ID and a Wikidata user ID are different identifiers.
A matching title, source ID, or old approval does not grant permission to edit a foreign item.
Do not run concurrent public correction workers against the same target.

## 3. Read the current evidence

Read these local files before the first correction:

- [Latest full audit](../../state/gila-refresh-2026-11-17/RESULTS.md).
- [Six current source reviews](../../state/gila-refresh-2026-11-17/six-current-source-reviews.json).
- [Six saved proof examples](../../state/gila-refresh-2026-11-17/six-current-choice-proofs.json).
- [Current source and ledger proof](../../state/gila-refresh-2026-11-17/current-source-ledger.json).
- [Canonical quality result](../../state/gila-refresh-2026-11-17/measurement-result.json).

The 17 November checks validated all six choices against the source and public target.
Those saved hashes, candidate keys, and target revisions can become stale.
Generate current proof at execution time.
If a file is absent, obtain the evidence again through normal reads.
Do not infer an absent payload from this table.

## 4. Apply the six Studio corrections

Each affected item has an old positional edit that places a work QID in a P528 catalogue code.
Remove that invalid edit and save the work identity through the source-choice workflow.
Keep the source catalogue code and its complete references.

| Source control number | Existing manuscript | Work target to revalidate | Historical edit index |
| --- | --- | --- | ---: |
| `990000645310205171` | [Q141746973](https://www.wikidata.org/wiki/Q141746973) | [Q141754916](https://www.wikidata.org/wiki/Q141754916) | 17 |
| `990000656400205171` | [Q141746983](https://www.wikidata.org/wiki/Q141746983) | [Q141755010](https://www.wikidata.org/wiki/Q141755010) | 16 |
| `990000665230205171` | [Q141746986](https://www.wikidata.org/wiki/Q141746986) | [Q141755388](https://www.wikidata.org/wiki/Q141755388) | 18 |
| `990000896070205171` | [Q141747178](https://www.wikidata.org/wiki/Q141747178) | [Q141754945](https://www.wikidata.org/wiki/Q141754945) | 17 |
| `990001028930205171` | [Q141747253](https://www.wikidata.org/wiki/Q141747253) | [Q141755028](https://www.wikidata.org/wiki/Q141755028) | 17 |
| `990001218770205171` | [Q141747501](https://www.wikidata.org/wiki/Q141747501) | [Q141754943](https://www.wikidata.org/wiki/Q141754943) | 17 |

The old indices are diagnostic hints. They are not current statement identifiers.
For every row, use local ID `QDraft_MS_<source control number>`.
Reconfirm the title, author, source occurrence, and folio against the current MARC record.

For each item:

1. Read the current source review and complete override.
2. Save a before snapshot with the source hash and current event version.
3. Confirm the exact invalid catalogue-code edit.
4. Mark the item unapproved.
5. Remove only the invalid edit.
6. Preserve unrelated labels, descriptions, aliases, exclusions, edits, and source choices.
7. Load the current source candidate and public work target.
8. Validate the work class, title, author, source record, and folio scope.
9. Save the source-specific work choice through the normal application service.
10. Verify the saved choice, override state, and event history.

Do not clear the entire override.
If an index also contains a valid edit, preserve that edit when you remove the invalid value.
If a step fails, leave the item unapproved and inspect the current state.
Do not publish an intermediate result.

### Existing application routes

Verify the deployed route definitions before use:

```text
GET   /api/runs/{run_id}/wikidata-studio/source-review?local_id={local_id}
PATCH /api/runs/{run_id}/wikidata-studio/items/{local_id}
POST  /api/runs/{run_id}/wikidata-studio/source-choice
```

The local schema check on 17 November rejects null values inside `statement_edits`.
The route contains a null-removal branch, but its request model does not accept that input.
Do not assume a null patch works on the deployed service.

The existing PATCH replaces a supplied index object while it preserves other index entries.
Supply the confirmed edit object with only the invalid value field removed.
Preserve every valid field from that object.
If no fields remain, an empty object is an accepted replacement in the local schema.
Rebuild and confirm that the source catalogue code returns.
Verify the deployed service's current schema and behavior before execution.

The source-choice POST needs current values for:

```text
local_id
control_number
candidate_key
expected_source_hash
target_qid
evidence_uri
decision = select
reason
```

Use the matching NLI evidence URL from the six source reviews.
The server must create the current proof and validate it again.
Do not submit a stored proof through the generic item PATCH.
Do not replace the normal source-choice service with a direct SQL update.

The source-choice service records the proof through `apply_event` and clears approval and old verdicts.
Verify those effects after each save.
The six items do not form one atomic transaction through these separate routes.

## 5. Rebuild and verify the six items

- [ ] Read the six overrides again from production.
- [ ] Confirm that no affected P528 value contains a QID.
- [ ] Confirm that each saved work choice belongs to the intended source occurrence.
- [ ] Confirm that unrelated curator fields remain intact.
- [ ] Confirm that the event history records each change and its real actor.
- [ ] Rebuild the affected canonical Studio items.
- [ ] Run the normal export quality gate.
- [ ] Confirm that all six `CATALOG_CODE_IS_QID` failures disappear.
- [ ] Review each P1574 link and its complete source references.
- [ ] Review and approve the rebuilt items through the normal workflow.

Use a full rebuild if a selected rebuild cannot establish the required corpus context.
If another quality defect appears, keep the quality gate closed and resolve it.
A six-item success does not certify the complete corpus.

## 6. Publish through the existing guarded workflow

Prepare the normal Publication preview and inspect its exact changes.
Retain the existing QIDs and all unrelated public statements.
Recheck the source, ownership, target revision, qualifiers, rank, and complete references before each send.
Use the normal authenticated Publication path for supported public updates.

For each accepted write, save the response revision and complete public readback.
Compare the readback with the expected full entity.
Count a correction only after this verification succeeds.
A changed source or target requires a fresh review.
A timeout or uncertain response requires normal recovery before any new send.

The Studio access restriction and Wikidata `maxlag` are separate conditions.
Restored Studio access permits the local source workflow; it does not promise that Wikidata accepts automatic edits.
Honor the saved retry date, `Retry-After`, and the existing backoff.
The foreground reference exception requires a separate explicit confirmation for one exact item.
It cannot serve an automatic batch or a description correction.

## 7. Continue the reference queue

First inspect the current process and checkpoint:

```text
state/gila-automatic-reference-2026-11-17/queue/progress.json
state/gila-automatic-reference-2026-11-17/queue/queue.json
state/gila-automatic-reference-2026-11-17/automatic-run.log
```

Do not start a second worker while the existing worker holds the queue lock.
Use the same candidate file, state directory, saved plans, and Publication execution after a normal process stop.
Investigate a blocked or uncertain result before another attempt.

Only after those checks, the existing launcher can resume the approved reference scope:

```bash
bash state/gila-automatic-reference-2026-11-17/run.sh --apply
```

This command performs live reference repairs when all guards permit them.
It does not apply the six Studio corrections.
Its default wait limit is 24 hours per item.
Exit code 75 means a resumable pause; it does not mean success.
Recheck the current checkpoint before you use any dated progress count.

## 8. Complete the other open work

- Review the generated English description on [Q141175733](https://www.wikidata.org/wiki/Q141175733).
- Recheck its creator proof and current source before a normal metadata correction.
- Preserve its other fields and add no unsupported biographical facts.
- Review the Hannover identity pair [Q134926101](https://www.wikidata.org/wiki/Q134926101) and [Q141754492](https://www.wikidata.org/wiki/Q141754492).
- Keep Q134926101 read-only; another user created it.
- Resolve the identity and source mapping before a ledger change.
- Review the 27 duplicate-reference items outside the existing queue scope.
- Review the remaining source, person, work, folio, library, date, script, and bibliography cases.
- Resolve the physical-manuscript/work model difference with Gila.

The 193 recovered original-source candidates require their normal source review and import process.
Keep the ten title-conflict cases excluded.
A source import invalidates old source hashes and can invalidate prepared choices.
Re-prove affected choices after an import.
Do not count an unresolved case as fixed merely because a note describes it.

## 9. Run the final full audit

1. Read the current source records and current ledger mappings.
2. Obtain complete public JSON for every known audit target.
3. Record each public read time and entity revision.
4. Compare all 897 source records against the current public data.
5. Verify exact duplicates and eligible repeated retrieval references independently.
6. Verify each correction receipt against the current public revision.
7. Export the nine-point acceptance packet and separate manual cases.

The 17 November baseline contained 3,537 known public entities and 15,096 main checks.
It verified seven earlier repair receipts.
It still had six Studio source defects and one unresolved manuscript mapping.
These are dated observations, not the required future result.
Known-entity coverage does not prove a complete global duplicate search.

Deliver:

- A list of checked QIDs and revisions.
- A list of corrected QIDs with exact before/after revisions and receipts.
- The nine-point requirement table.
- Separate source-absence, repair, manual-review, and unchecked inventories.
- A source hash manifest and a record of preserved foreign items.
- A list of any remaining publication or service delays.

Only claim full Gila acceptance when every requirement has current supporting evidence.
An unresolved item must remain visible in the final result.

## References for the next operator

- [Source-choice contract](../architecture/blocks/wikidata-studio/source-review.md).
- [Exact source scope](../architecture/blocks/wikidata-studio/source-choice-scope.md).
- [Six historical edit cases](../architecture/blocks/wikidata-studio/creation-review-cases.md).
- [Creator-only writes](../architecture/blocks/wikidata-studio/creator-only-writes.md).
- [Publication workflow](../architecture/blocks/wikidata-studio/production-publication.md).
- [Reference queue](../architecture/blocks/wikidata-studio/reference-repair-queue.md).
- [Durable retries](../architecture/blocks/wikidata-studio/repair-retries.md).
- [Completion packet](../architecture/blocks/wikidata-studio/gila-completion-packet.md).

Read the repository's `AGENTS.md` and the relevant rules before implementation.
Update documentation with any code change.
Obtain separate user permission before each GitHub action or deployment.
