# Manuscript repair workspace

Open [Wikidata Manuscript Audit and Repair](admin-gila-repairs.md).
Use the page for the audit, source review, Kimi review, reference queues, and Publication.
The workspace uses the existing workers and their saved results.

## Run all eligible repairs

1. Select **Repair all eligible items** to authorize the job to publish edits.
2. The job audits all nine requests against live Wikidata and the current MARC source.
3. The job rebuilds a stale canonical source once and repeats the audit.
4. The job checks the reviewed reference queues.
5. Kimi checks open scribe and production place cases against MARC and authority evidence.
6. The job rechecks the evidence and prepares the supported repairs.
7. The job approves the scoped release, runs the dry run, and publishes the ready updates.
8. The job audits live Wikidata again and displays the results on this page.

The job service saves each stage. Close or reload the page to return to the same repair.
The job rechecks administrator and project edit access before an automatic write.
The preparation worker selects exact source claims and excludes ambiguous claims and foreign targets.
If the dry run identifies blocked QIDs, the job prepares the remaining items in a new release.
Publication requires a confirmed first-revision creator and the existing revision and authorization checks.
An earlier uncertain edit requires evidence. The exact Troki repair can proceed if the earlier execution is idle, two reads confirm the original revision, and the author claim is still absent.
The plan for that retry uses the same base revision.
The read-back checks the new claims and creator against the current public revision (W-362).
The results show verified edits, reference corrections, exclusions, and the new audit.
The evidence download includes verified Publication receipts from automatic repairs.
The completed repair shows before and after case counts for all nine requirements.
The preparation worker binds each audited claim to the public revision it checked.
If the reference queue edits a public item, the job refreshes the audit before Publication.
Conflicts in physical form or scribe identity require source review.

Select **Repair people and current labels** for the [full authority and holding preflight](gila-authority-and-label-repair.md).

Select **Repair sources and inscriptions** to add [source evidence for requirement 8](gila-corpus-repair.md#add-source-evidence-for-requirement-8).

Open a requirement to see its verified count and remaining cases.
Use the filters to find missing source facts, uncertain identities, and cases that need reference evidence.
Use **Review with Kimi** to inspect the evidence for one supported case.
The automatic result shows the number of checked cases, supported cases, creator exclusions, and unavailable checks.
See [Kimi case review](gila-case-review.md) for source and authority requirements.
Provide evidence for unresolved records, passages, or model decisions before another audit.
Items that other accounts created are read-only.

## Prepare and publish a manuscript repair

1. Select **Prepare eligible release** to prepare eligible items for manual publication.
2. Open a case and select **Prepare repair release** if you need a specific case.
3. In **Repair releases**, approve the scoped release.
4. Run the dry run.
5. Publish the checked release when it is ready.
6. Read the execution progress and Publication history on the same page.
7. Select **Verify repairs on Wikidata** after a public edit.

The server checks the first-revision creator for each eligible item.
The preparation worker removes duplicate targets and checks the current source and exact target.
The batch lists items it excludes and counts cases that need review or evidence.
An empty selection creates no Publication release.
The exact author repair for Ḥizzuq Emunah retains its one-statement scope in a mixed release.
An active batch or its usable saved release can reopen on the same page.
The Publication worker checks creator UID 6935067 before it sends an edit.
The page can restore saved releases after a reload.
The scoped controls offer review, dry run, publish, retry, and history for the exact release.

If no release is ready, use **Review open cases** to inspect the evidence.
A Kimi result supplies evidence for review.
A local source decision shows the **Rebuild source and run audit** action on the same page.
The repair inventory shows the saved build and its local validation details.

## Repair references

Open the reference queues from the audit result or the controls below the result.
Select **Remaining references** or **Original queue**.
Confirm the scope before you start or resume.
The queue shows progress, outcomes, and items created by other accounts on the same page.
The job tray opens the applicable queue in the workspace.

Download the [verified-only export and the proven claim errors](gila-audit-exports.md) from the audit result.

## API and verification

- `admin_gila_repairs.py` serves `POST /api/admin/wikidata-gila-repairs/prepare-batch` and `GET /api/admin/wikidata-gila-repairs/preparations`.
- `POST /api/admin/wikidata-gila-repairs/repair-all` queues `wikidata_gila_repair`. The status route includes its current phase and final result.
- `wikidata_gila_repair_job.py` coordinates the existing jobs and retains their results. It uses a private, expiring credential grant.
- The batch route gives the existing preparation worker the eligible targets and current audit snapshot.
- The worker checks each target and records the results before it seals a mixed release.
- The `prepare-next` route remains available for existing clients.
- The list route pages through preparation jobs for audited repairs, including earlier jobs that saved the build snapshot.
- `ManuscriptRepairReleases.tsx` restores release progress and embeds `WikidataPublicationPanel.tsx` in scoped mode.
- `ReferenceRepairQueue.tsx` and `WikidataRepairInventory.tsx` provide the shared controls for the audit page and direct routes.
- `test_admin_gila_repairs.py` checks job admission, reuse, and saved releases.
- That suite also checks the automatic admission, exact claim selection, and final audit after a repair failure.
- `test_gila_exact_troki_author_repair.py` checks a mixed batch and a batch with zero owned targets.
- `admin-gila-repairs.spec.ts` checks preparation, approval, dry run, history, publish, and reference repair on the audit URL.
