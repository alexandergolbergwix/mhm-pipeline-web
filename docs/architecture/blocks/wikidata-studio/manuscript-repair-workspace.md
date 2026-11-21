# Manuscript repair workspace

Open [Wikidata Manuscript Audit and Repair](admin-gila-repairs.md).
Use the page for the audit, source review, Kimi review, reference queues, and Publication.
The workspace uses the existing workers and their saved results.

## Prepare and publish a manuscript repair

1. Select **Start manuscript repairs** to prepare the next eligible release.
2. Open a case and select **Prepare repair release** if you need a specific case.
3. In **Repair releases**, approve the scoped release.
4. Run the dry run.
5. Publish the checked release when it is ready.
6. Read the execution progress and Publication history on the same page.
7. Select **Verify repairs on Wikidata** after a public edit.

The server checks the first-revision creator before preparation.
The preparation worker checks the current source and the exact target.
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

## API and verification

- `admin_gila_repairs.py` serves `POST /api/admin/wikidata-gila-repairs/prepare-next` and `GET /api/admin/wikidata-gila-repairs/preparations`.
- The start route resumes active repair preparation or creates one scoped Publication release.
- The list route pages through preparation jobs for audited repairs, including earlier jobs that saved the build snapshot.
- `ManuscriptRepairReleases.tsx` restores release progress and embeds `WikidataPublicationPanel.tsx` in scoped mode.
- `ReferenceRepairQueue.tsx` and `WikidataRepairInventory.tsx` provide the shared controls for the audit page and direct routes.
- `test_admin_gila_repairs.py` checks job admission, reuse, and saved releases.
- `admin-gila-repairs.spec.ts` checks preparation, approval, dry run, history, publish, and reference repair on the audit URL.
