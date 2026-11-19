import {useState} from "react";
import {Link} from "react-router-dom";

import {ReferenceRepairs} from "@/api/referenceRepairs";
import {AdminLayout} from "@/components/admin/AdminLayout";
import {Glass} from "@/components/glass";
import {ReferenceRepairProgress} from "@/components/wikidata/ReferenceRepairProgress";
import {useReferenceRepairStatus} from "@/hooks/useReferenceRepairStatus";

export default function AdminReferenceRepairs() {
  const {state, error: refreshError, lastSync, refresh, acceptJob} = useReferenceRepairStatus();
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const queued = state?.job?.status === "queued";
  const active = queued || state?.job?.status === "running";
  const complete = state?.job?.status === "succeeded";
  const checkPreviousRepair = state?.halted_status === "blocked";
  const blocked = state?.halted_status && !checkPreviousRepair && state.halted_status !== "pre_send_retryable";

  async function start() {
    if (!state || !consent || busy || active || complete || blocked || refreshError) return;
    setBusy(true);
    setError("");
    try {
      const job = await ReferenceRepairs.start(state.scope_digest);
      acceptJob(job);
      setConsent(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The queue refused this request.");
      setConsent(false);
    } finally {
      setBusy(false);
    }
  }

  return <AdminLayout><Glass as="section" className="p-6 space-y-5">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="space-y-2"><div className="kicker">Admin · Backend job</div>
        <h1 className="text-2xl font-semibold">Wikidata reference repairs</h1>
        <p className="text-sm muted">The backend runs the reviewed queue. You can close this page after the job starts.</p>
      </div>
      <button className="button-ghost text-sm" onClick={refresh}>Refresh now</button>
    </div>
    {refreshError && <p role="alert" className="text-danger">Status refresh failed: {refreshError}{state ? " The last confirmed progress remains below." : ""}</p>}
    {!state && !refreshError && <p role="status">Please wait for the queue status.</p>}
    {state && <>
      <ReferenceRepairProgress state={state} lastSync={lastSync} connectionError={Boolean(refreshError)} />
      <Link className="text-biu-sky underline text-sm" to={`/runs/${state.run_id}/wikidata-studio/repair-plan`}>View saved repair inventory</Link>
      {!state.enabled && <p>The backend handoff is not enabled.</p>}
      {blocked && <p>The previous outcome requires inspection before another start.</p>}
      {checkPreviousRepair && <p>The backend can resume if the previous repair matches the live item.</p>}
      {state.job?.error && <p className="text-danger">{state.job.error}</p>}
    </>}
    {state && !active && !complete && <label className="flex gap-2 items-center text-sm"><input type="checkbox" checked={consent}
      onChange={event => setConsent(event.target.checked)} />
      I authorize the reviewed reference repairs only.</label>}
    <button className="button-primary" disabled={!state?.enabled || !consent || busy || active || complete || Boolean(blocked) || Boolean(refreshError)}
      onClick={() => void start()}>{busy ? "Please wait…" : queued ? "Job in queue" : active ? "Backend repairs are active"
        : complete ? "Reference queue complete" : checkPreviousRepair ? "Check previous repair and resume" : "Start backend reference repairs"}</button>
    {error && <p role="alert" className="text-danger">{error} No automatic resend will occur.</p>}
    <details className="text-sm"><summary className="cursor-pointer">Repair scope and safeguards</summary>
      <div className="space-y-2 mt-3 muted">
        <p>Only items created by Alexander Goldberg IL can change. The worker checks creator UID 6935067 before each write.</p>
        <p>The queue removes equivalent reference blocks. It preserves values, qualifiers, ranks, distinct sources, and maxlag=5.</p>
        <p>The six Studio corrections, source imports, and identity decisions remain outside this queue.</p>
        <p>A verified receipt records a past correction. It does not prove that all Gila requirements now pass.</p>
      </div>
    </details>
  </Glass></AdminLayout>;
}
