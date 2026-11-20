import {useState} from "react";
import {Link, useSearchParams} from "react-router-dom";

import {ReferenceRepairs, type ReferenceScope} from "@/api/referenceRepairs";
import {AdminLayout} from "@/components/admin/AdminLayout";
import {Glass} from "@/components/glass";
import {ReferenceRepairProgress} from "@/components/wikidata/ReferenceRepairProgress";
import {useReferenceRepairStatus} from "@/hooks/useReferenceRepairStatus";

export default function AdminReferenceRepairs() {
  const [params, setParams] = useSearchParams();
  const scope = params.get("scope") === "remaining" ? "remaining" : "original";
  return <AdminLayout>
    <nav aria-label="Reference repair scope" className="mb-4 flex flex-wrap gap-2">
      <button className={scope === "original" ? "button-primary" : "button-ghost"} aria-pressed={scope === "original"}
        onClick={() => setParams({})}>Original queue</button>
      <button className={scope === "remaining" ? "button-primary" : "button-ghost"} aria-pressed={scope === "remaining"}
        onClick={() => setParams({scope: "remaining"})}>Remaining references</button>
    </nav>
    <ReferenceRepairScope key={scope} scope={scope} />
  </AdminLayout>;
}

function ReferenceRepairScope({scope}: {scope: ReferenceScope}) {
  const {state, error: refreshError, lastSync, refresh, acceptJob} = useReferenceRepairStatus(scope);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = state?.job?.status === "queued" || state?.job?.status === "running";
  const complete = state?.job?.status === "succeeded";
  const resume = state?.resume_action === "resume";
  const checkPreviousRepair = state?.resume_action === "check_repair";
  const blocked = state?.resume_action === "review";
  const currentItem = state?.halted_qid;
  const legacyStopMessage = state?.job?.error === "The reference queue paused or requires review.";
  const legacyWriteMessage = state?.job?.error === "Review the result of the last Wikidata write before another attempt.";
  const showAction = state?.enabled && !active && !complete && !blocked && !state.other_scope_active;
  const replaceStopMessage = (legacyStopMessage && (resume || checkPreviousRepair || blocked))
    || (legacyWriteMessage && checkPreviousRepair);
  const stopMessage = replaceStopMessage ? null : state?.job?.error;
  const actionLabel = checkPreviousRepair ? "Check previous repair and resume"
    : resume ? "Resume reference repairs" : "Start backend reference repairs";

  async function start() {
    if (!state || !showAction || !consent || busy || refreshError) return;
    setBusy(true);
    setError("");
    try {
      const job = await ReferenceRepairs.start(state.scope_digest, scope);
      acceptJob(job);
      setConsent(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The queue refused this request.");
      setConsent(false);
    } finally {
      setBusy(false);
    }
  }

  const inventory = state?.reference_inventory;
  const otherCreators = inventory?.items.filter(item => !item.creator_matches) ?? [];
  return <Glass as="section" contentClassName="space-y-5">
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold sm:text-3xl">Wikidata reference repairs</h1>
        <p className="text-sm muted">The queue runs even if you close this page.</p>
      </div>
      <button className="button-ghost shrink-0 text-sm" onClick={refresh}>Refresh now</button>
    </header>
    {refreshError && <p role="alert" className="rounded-xl bg-[var(--danger-bg)] px-4 py-3 text-sm text-danger">
      Status refresh failed: {refreshError}{state ? " The page shows the last confirmed progress." : ""}
    </p>}
    {!state && !refreshError && <p role="status">Please wait for the queue status.</p>}
    {state && <>
      {state.other_scope_active && <p role="status" className="text-sm muted">The other reference queue is active. Wait for it to finish before you start this scope.</p>}
      {inventory && <div className="rounded-xl border border-glass-line p-4 text-sm">
        <p className="font-medium">{inventory.items.length} audited items · {inventory.items.reduce((sum, item) => sum + item.baseline_blocks, 0)} repeated references at audit</p>
        <p className="mt-1 muted">{state.total} items in the repair queue. {otherCreators.length} items have other creators and require separate review.</p>
        {inventory.live_checked_at && <p className="mt-1 muted">Latest audit · {new Date(inventory.live_checked_at).toLocaleString()}</p>}
        <Link className="link-accent mt-1 inline-block underline" to="/admin/wikidata-gila-repairs">Open the manuscript audit</Link>
        <details className="mt-3">
          <summary className="cursor-pointer">View the audited items</summary>
          <ul className="mt-2 grid gap-x-6 gap-y-1 sm:grid-cols-2 lg:grid-cols-3">
            {inventory.items.map(item => <li key={item.qid} className="flex flex-wrap items-baseline gap-x-2">
              <a className="link-accent underline" href={`https://www.wikidata.org/wiki/${item.qid}`} target="_blank" rel="noreferrer">{item.qid}</a>
              <span className="muted">{item.observed_blocks == null ? `${item.baseline_blocks} at first audit` : `${item.observed_blocks} at latest audit`} · {
                item.queue_result === "verified" ? "Repair verified" : item.queue_result === "already_clean"
                  ? "Already clear" : item.creator_matches ? "In queue" : "Other creator"
              }</span>
            </li>)}
          </ul>
        </details>
      </div>}
      <ReferenceRepairProgress state={state} lastSync={lastSync} connectionError={Boolean(refreshError)}>
        {(!state.enabled || blocked || showAction || stopMessage || error) &&
          <div className="space-y-3 rounded-b-2xl border-t border-glass-line bg-[var(--surface-inset)] p-4 text-sm sm:px-6">
            {!state.enabled && <p className="muted">This environment does not allow reference repairs.</p>}
            {blocked && <p>Review the previous result{currentItem && <> for <span className="font-mono">{currentItem}</span></>} before you restart the queue.</p>}
            {checkPreviousRepair && showAction &&
              <p>The worker can resume if the previous repair{currentItem && <> for <span className="font-mono">{currentItem}</span></>} matches the live item.</p>}
            {resume && currentItem && showAction &&
              <p>Resume from <span className="font-mono">{currentItem}</span>. The worker checks the saved result and current item before any edit.</p>}
            {stopMessage && <p className="text-danger">{stopMessage}</p>}
            {showAction && <div className="flex flex-col items-start gap-3 lg:flex-row lg:flex-wrap lg:items-center lg:justify-between">
              <label className="flex min-h-10 cursor-pointer items-center gap-2">
                <input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} />
                I authorize the reviewed reference repairs only.
              </label>
              <button className="button-primary w-full sm:w-auto" disabled={!consent || busy || Boolean(refreshError)}
                onClick={() => void start()}>{busy ? "Please wait…" : actionLabel}</button>
            </div>}
            {error && <p role="alert" className="text-danger">{error} No automatic resend will occur.</p>}
          </div>}
      </ReferenceRepairProgress>
      <Link className="link-accent inline-block text-sm font-medium underline underline-offset-4" to={`/runs/${state.run_id}/wikidata-studio/repair-plan`}>
        View saved repair inventory
      </Link>
    </>}
    <details className="border-t border-glass-line pt-3 text-sm">
      <summary className="cursor-pointer font-medium">Repair scope and safeguards</summary>
      <div className="space-y-2 mt-3 muted">
        <p>Only items created by Alexander Goldberg IL can change. The worker checks creator UID 6935067 before each write.</p>
        <p>The queue removes equivalent reference blocks. It preserves values, qualifiers, ranks, and distinct sources.</p>
        {state && <p>The queue uses maxlag={state.maxlag} for Wikidata edits.</p>}
        <p>The six Studio corrections, source imports, and identity decisions remain outside this queue.</p>
        <p>A verified receipt records a past correction. It does not prove that all Gila requirements now pass.</p>
      </div>
    </details>
  </Glass>;
}
