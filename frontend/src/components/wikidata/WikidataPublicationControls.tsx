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
  busyCommand?: PublicationAdvanceCommand["type"] | null;
  error?: string | null;
}

function shortDigest(digest: string): string {
  const value = digest.startsWith("sha256:") ? digest.slice(7) : digest;
  return value.length > 12 ? `${value.slice(0, 12)}…` : value;
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
  busyCommand = null,
  error = null,
}: WikidataPublicationControlsProps) {
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
  const busy = busyCommand !== null;
  const executionActive = execution?.status === "queued" || execution?.status === "running";
  const selectedKeys = consentSelection.planDigest === plan?.plan_digest ? consentSelection.entityKeys : [];
  const consents = (plan?.blocked_actions ?? []).flatMap((action) =>
    action.consent && selectedKeys.includes(action.entity_key) ? [action.consent] : []);

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
          <div className="kicker">Approval Set</div>
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
          <div className="kicker">Dry-run Receipt</div>
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
        Override cache (fresh Wikidata checks)
      </label>
      <p className="text-xs muted">A normal dry-run reuses a current saved plan. Override cache checks every entity again.</p>
      {plan && <div className="rounded-lg border border-white/10 p-3 space-y-2" data-testid="publication-plan-results">
        <p>{plan.action_counts.create ?? 0} creates · {plan.action_counts.update ?? 0} updates · {plan.action_counts.blocked ?? 0} blocked</p>
        <p className="text-xs muted">Saved results remain visible after refresh. Expired receipts require fresh checks before publication.</p>
        {!!plan.blocked_actions?.length && <details open>
          <summary>Blocked actions (first {plan.blocked_actions.length})</summary>
          <ul className="space-y-2 mt-2">{plan.blocked_actions.map((action) => <li key={action.entity_key} className="text-xs">
            <span className="font-semibold">{action.entity_key}{action.target_qid ? ` · ${action.target_qid}` : ""}</span>
            <p>{action.reason}</p>
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
              <p className="muted">Create a new Dry-run Receipt to check this consent. A changed item requires another review.</p>
            </div>}
          </li>)}</ul>
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
          {busyCommand === "review" ? commandLabel("review") : `Approve eligible Release (${release.entity_count.toLocaleString()})`}
        </button>
        {pageEntityKeys.length > 0 && (
          <button
            type="button"
            className="button-ghost text-sm"
            disabled={busy || !publication.source_current || executionActive}
            onClick={() => { void reviewPage(); }}
            data-testid="publication-review-page"
          >
            Approve this page ({pageEntityKeys.length})
          </button>
        )}
        <button
          type="button"
          className="button-ghost text-sm"
          disabled={busy || !publication.source_current || !readiness.approvalCurrent || release.finding_counts.error > 0 || executionActive}
          onClick={() => { void dryRun(); }}
          data-testid="publication-dry-run"
        >
          {busyCommand === "dry_run" ? commandLabel("dry_run") : "Create Dry-run Receipt"}
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
        <a className="button-ghost text-sm ml-auto" href={auditHref} data-testid="publication-audit-link">
          Open audit
        </a>
      </div>

      {execution && (
        <div className="rounded-lg border border-white/10 p-3 space-y-2" data-testid="publication-execution-progress">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="kicker">Execution</div>
              <p className="text-sm text-ink">
                {execution.processed.toLocaleString()} of {execution.total.toLocaleString()}
              </p>
            </div>
            <span className="rounded-full border border-white/10 px-2 py-1 text-xs muted">{execution.status}</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-white/10" aria-label="Execution progress">
            <div
              className="h-full bg-biu-sky transition-[width]"
              style={{width: `${execution.total > 0 ? Math.min(100, execution.processed / execution.total * 100) : 0}%`}}
            />
          </div>
          <p className="text-xs muted">
            {execution.succeeded.toLocaleString()} succeeded · {execution.failed.toLocaleString()} failed · {execution.skipped.toLocaleString()} skipped
          </p>
          {execution.current_entity_label && (
            <p className="text-xs muted">Current entity: <span className="text-ink">{execution.current_entity_label}</span></p>
          )}
        </div>
      )}

      {error && <p className="text-sm text-danger" role="alert">{error}</p>}
    </div>
  );
}
