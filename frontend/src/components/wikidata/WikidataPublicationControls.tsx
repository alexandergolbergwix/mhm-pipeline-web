import {WikidataPublicationAiReview} from "./WikidataPublicationAiReview";
import {useState} from "react";
import type {
  PublicationAdvanceCommand,
  PublicationEntity,
  PublicationSummary,
} from "@/api/publication";
import {getPublicationReadiness} from "@/utils/publicationState";

export interface WikidataPublicationControlsProps {
  publication: PublicationSummary;
  entities: PublicationEntity[];
  onAdvance: (command: PublicationAdvanceCommand) => void | Promise<void>;
  auditHref: string;
  onPrepareNewRelease?: () => void | Promise<void>;
  onUseExisting?: (entityKeys: string[]) => void | Promise<void>;
  busyCommand?: PublicationAdvanceCommand["type"] | null;
  error?: string | null;
}

function shortDigest(digest: string): string {
  const value = digest.startsWith("sha256:") ? digest.slice(7) : digest;
  return value.length > 12 ? `${value.slice(0, 12)}…` : value;
}

function actionCount(counts: Record<string, number>, key: string): number {
  return counts[key] ?? 0;
}

function commandLabel(type: PublicationAdvanceCommand["type"]): string {
  if (type === "review") return "Saving review…";
  if (type === "dry_run") return "Checking Plan…";
  if (type === "publish") return "Starting publication…";
  if (type === "resume") return "Resuming…";
  return "Cancelling…";
}

export function WikidataPublicationControls({
  publication,
  entities,
  onAdvance,
  auditHref,
  onPrepareNewRelease,
  onUseExisting,
  busyCommand = null,
  error = null,
}: WikidataPublicationControlsProps) {
  const [aiActive, setAiActive] = useState(false);
  const [forceRefresh, setForceRefresh] = useState(false);
  const [consentSelection, setConsentSelection] = useState<{planDigest: string; entityKeys: string[]}>({
    planDigest: "", entityKeys: [],
  });
  const release = publication.current_release;
  const approval = publication.approval_set;
  const plan = publication.plan;
  const receipt = publication.dry_run_receipt;
  const execution = publication.execution;
  const readiness = getPublicationReadiness(publication);
  const pageEntityKeys = entities
    .filter((entity) => entity.review_status !== "approved")
    .map((entity) => entity.entity_id);
  const busy = busyCommand !== null || aiActive;
  const executionActive = execution?.status === "queued" || execution?.status === "running";
  const executionFailed = execution?.status === "failed";
  const executionFinishedWithErrors = execution?.status === "failed"
    && execution.processed >= execution.total;
  const executionWaitingForReview = execution?.status === "paused"
    && execution.total > 0
    && execution.processed >= execution.total;
  const selectedKeys = consentSelection.planDigest === plan?.plan_digest ? consentSelection.entityKeys : [];
  const consents = (plan?.blocked_actions ?? []).flatMap((action) =>
    action.consent && selectedKeys.includes(action.entity_key) ? [action.consent] : []);
  const includedCount = plan
    ? actionCount(plan.action_counts, "create")
      + actionCount(plan.action_counts, "update")
      + actionCount(plan.action_counts, "skip")
    : 0;
  const omittedCount = actionCount(plan?.action_counts ?? {}, "blocked");

  const reviewEligibleRelease = () => onAdvance({
    type: "review",
    release_id: release.release_id,
    expected_release_digest: release.release_digest,
    selection: {mode: "eligible_release"},
    decision: "approve",
    reason: "Curator approved the eligible Release manifest.",
  });
  const reviewPage = () => onAdvance({
    type: "review",
    release_id: release.release_id,
    expected_release_digest: release.release_digest,
    selection: {mode: "entities", entity_keys: pageEntityKeys},
    decision: "approve",
    reason: "Curator approved the current entity page.",
  });
  const dryRun = async () => {
    if (!approval) return;
    await onAdvance({
      type: "dry_run",
      approval_set_id: approval.approval_set_id,
      expected_approval_digest: approval.approval_digest,
      ...(forceRefresh ? {force_refresh: true} : {}),
      ...(consents.length ? {foreign_qid_consents: consents} : {}),
    });
    setConsentSelection({planDigest: "", entityKeys: []});
  };
  const publish = () => {
    if (!plan || !receipt) return;
    return onAdvance({
      type: "publish",
      plan_id: plan.plan_id,
      dry_run_receipt_id: receipt.dry_run_receipt_id,
      expected_receipt_digest: receipt.receipt_digest,
    });
  };

  return (
    <div className="space-y-4" data-testid="wikidata-publication-controls">
      <nav aria-label="Publication steps" className="flex gap-4 text-sm">
        <span aria-current={!readiness.approvalCurrent ? "step" : undefined}>1. Prepare</span>
        <span aria-current={!execution && readiness.approvalCurrent && !readiness.publishAllowed ? "step" : undefined}>2. Review</span>
        <span aria-current={readiness.publishAllowed || execution ? "step" : undefined}>3. Publish</span>
      </nav>
      <div className="rounded-lg bg-white/5 p-4 space-y-3">
        <p className="text-lg font-medium">{executionFailed ? executionFinishedWithErrors ? "Upload finished with errors" : "Upload stopped" : execution ? "Publication progress" : readiness.publishAllowed ? "Ready to publish" : !publication.source_current ? "Source changed" : !readiness.approvalCurrent ? "Prepare your items" : plan ? "Resolve items before publication" : "Check your items"}</p>
        <p className="text-sm muted">{release.entity_count} items in this Release · Target: {publication.target === "live" ? "www.wikidata.org" : "test.wikidata.org"}</p>
        {plan && <p className="text-sm">{actionCount(plan.action_counts, "create")} new · {actionCount(plan.action_counts, "update")} updates · {actionCount(plan.action_counts, "skip")} reused without updates · {actionCount(plan.action_counts, "blocked")} need attention</p>}
        {plan && <div className="rounded-md border border-white/10 bg-black/10 p-3" data-testid="publication-result-summary" aria-live="polite">
          <p className="text-sm font-medium">{includedCount.toLocaleString()} of {release.entity_count.toLocaleString()} records prepared</p>
          <p className="text-xs muted">
            {omittedCount.toLocaleString()} record{omittedCount === 1 ? "" : "s"} not included in this publication.
          </p>
        </div>}
        {!!plan?.blocked_actions?.length && <p className="text-sm text-warn">
          {plan.blocked_actions.filter(action => action.consent).length} identity checks · {plan.blocked_actions.filter(action => !action.consent).length} other checks need attention. Open the details for individual reasons.
        </p>}
        {release.finding_counts.error > 0 && <p className="text-warn">{release.finding_counts.error} source errors require attention. Open the details below.</p>}
        <p className="text-xs muted">Approval and publication readiness are separate. Records and connections outside the plan remain available for later review.</p>
        {!execution && (!readiness.approvalCurrent || !plan || readiness.publishAllowed || !plan.action_counts.blocked) && <button type="button" className="button-primary"
          disabled={busy || !publication.source_current || release.finding_counts.error > 0}
          onClick={() => {void (readiness.publishAllowed ? publish() : !readiness.approvalCurrent ? reviewEligibleRelease() : dryRun());}}>
          {busyCommand ? commandLabel(busyCommand) : readiness.publishAllowed ? "Publish to Wikidata" : !readiness.approvalCurrent ? "Approve Release for checks" : "Check before publish"}
        </button>}
      </div>
      {plan && <details open={aiActive || (!readiness.publishAllowed && !execution && !!plan.action_counts.blocked)}><summary className="cursor-pointer text-sm">Automatic preparation</summary><WikidataPublicationAiReview
        key={`${publication.publication_id}:${plan.plan_id}`} publication={publication}
        busy={busyCommand !== null} onAdvance={onAdvance} onActiveChange={setAiActive} /></details>}
      <details className="rounded-lg border border-white/10 p-3 space-y-3">
        <summary className="cursor-pointer text-sm">Publication details and manual actions</summary>
      <div className="grid gap-3 md:grid-cols-3">
        <div className="rounded-lg border border-white/10 bg-white/[0.03] p-3" data-testid="publication-release-state">
          <div className="flex items-center justify-between gap-2">
            <div className="kicker">Release</div>
            <span className={publication.source_current ? "text-xs text-success" : "text-xs text-warn"}>
              {publication.source_current ? "current" : "stale"}
            </span>
          </div>
          <p className="mt-1 text-sm text-ink">
            Release {release.revision} · {release.entity_count.toLocaleString()} entities
          </p>
          <p className="mt-1 font-mono text-[11px] muted" title={release.release_digest}>
            {shortDigest(release.release_digest)}
          </p>
          <p className="mt-2 text-xs muted">
            {release.finding_counts.error} errors · {release.finding_counts.warning} warnings
          </p>
        </div>

        <div className="rounded-lg border border-white/10 bg-white/[0.03] p-3" data-testid="publication-approval-state">
          <div className="kicker">Review</div>
          <p className={readiness.approvalCurrent ? "mt-1 text-sm text-success" : "mt-1 text-sm text-warn"}>
            {readiness.approvalCurrent ? "current" : approval ? "stale or incomplete" : "not created"}
          </p>
          <p className="mt-1 text-xs muted">{readiness.approvalReason}</p>
          {approval && (
            <p className="mt-2 text-xs muted">
              {approval.approved_count.toLocaleString()} approved · {approval.pending_count.toLocaleString()} pending
            </p>
          )}
        </div>

        <div className="rounded-lg border border-white/10 bg-white/[0.03] p-3" data-testid="publication-dry-run-receipt-state">
          <div className="kicker">Pre-publication check</div>
          <p className={readiness.receiptCurrent ? "mt-1 text-sm text-success" : "mt-1 text-sm text-warn"}>
            {readiness.receiptCurrent ? "current" : receipt?.status === "failed" ? "failed" : receipt ? "stale" : "not created"}
          </p>
          <p className="mt-1 text-xs muted">{readiness.receiptReason}</p>
          {plan && <p className="mt-2 text-xs muted">Plan {shortDigest(plan.plan_digest)}</p>}
        </div>
      </div>

      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={forceRefresh} disabled={busy || executionActive}
          onChange={(event) => setForceRefresh(event.target.checked)} />
        Check all records again
      </label>
      <p className="text-xs muted">The normal check reuses saved results. Use this option when you need fresh Wikidata checks.</p>
      {plan && <div className="rounded-lg border border-white/10 p-3 space-y-2" data-testid="publication-plan-results">
        <p>{actionCount(plan.action_counts, "create")} creates · {actionCount(plan.action_counts, "update")} updates · {actionCount(plan.action_counts, "blocked")} blocked · {actionCount(plan.action_counts, "skip")} existing items without updates</p>
        <p className="text-xs muted">Saved results remain visible after refresh. Expired receipts require fresh checks before publication.</p>
        {!!plan.blocked_actions?.length && <details>
          <summary>Blocked actions (first {plan.blocked_actions.length})</summary>
          <ul className="space-y-2 mt-2">
            {plan.blocked_actions.map((action) => <li key={action.entity_key} className="text-xs">
              <span className="font-semibold">{action.entity_key}{action.target_qid ? ` · ${action.target_qid}` : ""}</span>
              <details>
                <summary className="cursor-pointer">Why this record is not included</summary>
                <p className="break-words">{action.reason}</p>
              </details>
              {action.consent && <div className="mt-2 space-y-1">
                <a href={`https://${publication.target === "live" ? "www" : "test"}.wikidata.org/wiki/${action.consent.qid}`}
                  target="_blank" rel="noopener noreferrer" className="text-accent underline">Review {action.consent.qid}</a>
                <label className="flex items-center gap-2">
                  <input type="checkbox" checked={selectedKeys.includes(action.entity_key)}
                    disabled={busy || executionActive || !readiness.approvalCurrent || !publication.source_current}
                    onChange={(event) => {
                      const entityKeys = event.target.checked
                        ? [...selectedKeys, action.entity_key]
                        : selectedKeys.filter((key) => key !== action.entity_key);
                      setConsentSelection({planDigest: plan.plan_digest, entityKeys});
                    }} />
                  I reviewed {action.consent.qid} and permit this Release to update it.
                </label>
                {onUseExisting && <div className="space-y-1">
                  <p>Use this option only if both records describe the same item. A new Release requires approval and a fresh dry-run.</p>
                  <button type="button" className="button-ghost text-sm"
                    disabled={busy || !!execution || !readiness.approvalCurrent || !publication.source_current}
                    onClick={() => { void onUseExisting([action.entity_key]); }}>
                    Use {action.consent.qid} without updates
                  </button>
                </div>}
                <p className="muted">Check before publication after you review this item. A changed item requires another review.</p>
              </div>}
            </li>)}
          </ul>
        </details>}
      </div>}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="button-ghost text-sm"
          disabled={busy || !publication.source_current || executionActive}
          onClick={() => { void reviewEligibleRelease(); }}
          data-testid="publication-review-eligible-release"
        >
          {busyCommand === "review" ? commandLabel("review") : `Review all records (${release.entity_count.toLocaleString()})`}
        </button>
        {pageEntityKeys.length > 0 && (
          <button
            type="button"
            className="button-ghost text-sm"
            disabled={busy || !publication.source_current || executionActive}
            onClick={() => { void reviewPage(); }}
            data-testid="publication-review-page"
          >
            Review visible records ({pageEntityKeys.length})
          </button>
        )}
        <button
          type="button"
          className="button-ghost text-sm"
          disabled={busy || !publication.source_current || !readiness.approvalCurrent || release.finding_counts.error > 0 || executionActive}
          onClick={() => { void dryRun(); }}
          data-testid="publication-dry-run"
        >
          {busyCommand === "dry_run" ? commandLabel("dry_run") : "Check before publication"}
        </button>
        <button
          type="button"
          className="button-primary text-sm"
          disabled={busy || !publication.source_current || !readiness.publishAllowed || executionActive}
          onClick={() => { void publish(); }}
          data-testid="publication-publish"
        >
          {busyCommand === "publish" ? commandLabel("publish") : `Publish to ${publication.target === "live" ? "www.wikidata.org" : "test.wikidata.org"}`}
        </button>
        <a className="button-ghost text-sm ml-auto" href={auditHref} data-testid="publication-audit-link">
          Publication history
        </a>
      </div>

      </details>

      <div className="flex gap-2">
        {execution?.status === "paused" && (
          <button
            type="button"
            className="button-ghost text-sm"
            disabled={busy}
            onClick={() => { void onAdvance({type: "resume", execution_id: execution.execution_id}); }}
            data-testid="publication-resume"
          >
            {busyCommand === "resume" ? commandLabel("resume") : "Resume Execution"}
          </button>
        )}
        {executionActive && (
          <button
            type="button"
            className="button-ghost text-sm text-warn"
            disabled={busy}
            onClick={() => { void onAdvance({type: "cancel", operation_id: execution.execution_id}); }}
            data-testid="publication-cancel"
          >
            {busyCommand === "cancel" ? commandLabel("cancel") : "Cancel Execution"}
          </button>
        )}
      </div>
      {execution && (
        <div className="rounded-lg border border-white/10 p-3 space-y-2" data-testid="publication-execution-progress" aria-live="polite">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="kicker">{executionFailed ? executionFinishedWithErrors ? "Upload finished with errors" : "Upload stopped" : "Upload progress"}</div>
              <p className="text-sm text-ink">
                {execution.processed.toLocaleString()} of {execution.total.toLocaleString()}
              </p>
            </div>
            <span className="rounded-full border border-white/10 px-2 py-1 text-xs muted">{execution.status}</span>
          </div>
          {execution.status === "paused" && (
            <p className="text-sm text-warn">{executionWaitingForReview
              ? "All safe items finished. Resume to retry the unresolved items."
              : "The upload paused before it finished. Resume to continue from the saved state."}</p>
          )}
          <div className="h-2 overflow-hidden rounded-full bg-white/10" aria-label="Execution progress">
            <div
              className="h-full bg-biu-sky transition-[width]"
              style={{width: `${execution.total > 0 ? Math.min(100, execution.processed / execution.total * 100) : 0}%`}}
            />
          </div>
          <p className="text-xs muted">
            {execution.succeeded.toLocaleString()} succeeded · {execution.failed.toLocaleString()} failed · {execution.skipped.toLocaleString()} skipped
          </p>
          {executionFailed && (
            <div className="rounded-md border border-danger/30 bg-danger/5 p-3 space-y-2" data-testid="publication-failed-recovery" role="status">
              <p className="text-sm text-danger">
                {executionFinishedWithErrors ? "The upload finished with " : "This upload stopped after "}
                {execution.failed.toLocaleString()} {execution.failed === 1 ? "item failed" : "items failed"}.
                {execution.succeeded > 0 && ` ${execution.succeeded.toLocaleString()} items succeeded.`}
              </p>
              <p className="text-xs muted">{executionFinishedWithErrors
                ? "Review the audit, then start a new Release to retry the failed items."
                : "Start a new Release to use the latest fixes and try the upload again."}</p>
              {onPrepareNewRelease && (
                <button
                  type="button"
                  className="button-primary text-sm"
                  disabled={busy}
                  onClick={() => { void onPrepareNewRelease(); }}
                  data-testid="publication-start-new-release"
                >
                  Start a new Release
                </button>
              )}
            </div>
          )}
          {execution.error && (
            <p className="text-sm text-danger" role="alert">{execution.error}</p>
          )}
          {execution.current_entity_label && (
            <p className="text-xs muted">Current entity: <span className="text-ink">{execution.current_entity_label}</span></p>
          )}
        </div>
      )}

      {error && <p className="text-sm text-danger" role="alert">{error}</p>}
    </div>
  );
}
