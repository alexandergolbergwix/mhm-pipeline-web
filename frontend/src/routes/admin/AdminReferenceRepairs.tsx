import {useEffect, useRef, useState} from "react";
import {Link} from "react-router-dom";

import {api} from "@/api/client";
import {AdminLayout} from "@/components/admin/AdminLayout";
import {Glass} from "@/components/glass";

interface ReferenceStatus {
  enabled: boolean;
  scope_digest: string;
  run_id: string;
  total: number;
  verified: number;
  halted_status: string | null;
  job: {
    id: string;
    status: string;
    progress: {phase?: string; current_qid?: string; next_attempt_at?: string};
    error?: string | null;
  } | null;
}

const endpoint = "/admin/wikidata-reference-repairs";

export default function AdminReferenceRepairs() {
  const [state, setState] = useState<ReferenceStatus | null>(null);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const sequence = useRef(0);

  useEffect(() => {
    let stopped = false;
    async function refresh() {
      const request = ++sequence.current;
      try {
        const status = await api.get<ReferenceStatus>(endpoint);
        if (!stopped && request === sequence.current) setState(status);
      } catch (cause) {
        if (!stopped && request === sequence.current) {
          setState(null);
          setError(cause instanceof Error ? cause.message : "The queue status request failed.");
        }
      }
    }
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => { stopped = true; ++sequence.current; clearInterval(timer); };
  }, []);

  const active = state?.job?.status === "queued" || state?.job?.status === "running";
  const complete = state?.job?.status === "succeeded";
  const blocked = state?.halted_status && state.halted_status !== "pre_send_retryable";

  async function start() {
    if (!state || !consent || busy) return;
    setBusy(true);
    setError("");
    ++sequence.current;
    try {
      await api.post(endpoint + "/start", {confirmed: true, scope_digest: state.scope_digest});
      const status = await api.get<ReferenceStatus>(endpoint);
      setState(status);
      setConsent(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The queue refused this request.");
      setConsent(false);
    } finally {
      setBusy(false);
    }
  }

  return <AdminLayout><Glass as="section" className="p-6 space-y-4">
    <div className="kicker">Admin · Backend job</div>
    <h1 className="text-2xl font-semibold">Wikidata reference repairs</h1>
    <p>The backend runs the reviewed queue. You can close this page after the job starts.</p>
    <p>Only items created by Alexander Goldberg IL can change. The worker checks creator UID 6935067 before each write.</p>
    <p>This queue removes equivalent reference blocks. It preserves values, qualifiers, ranks, distinct sources, and maxlag=5.</p>
    <p>The six Studio corrections, source imports, and identity decisions remain outside this queue.</p>
    {state && <>
      <Link className="text-biu-sky underline" to={`/runs/${state.run_id}/wikidata-studio/repair-plan`}>View saved repair inventory</Link>
      <p role="status">{state.verified} verified correction receipts out of {state.total} reviewed items.</p>
      <p>Backend job: {state.job?.status ?? "not started"}. {state.job?.progress.phase ?? ""}</p>
      {state.job?.progress.current_qid && <p>Current item: {state.job.progress.current_qid}</p>}
      {state.job?.progress.next_attempt_at && <p>Saved retry time: {state.job.progress.next_attempt_at}</p>}
      {!state.enabled && <p>The backend handoff is not enabled.</p>}
      {blocked && <p>The previous outcome requires inspection before another start.</p>}
      {state.job?.error && <p className="text-danger">{state.job.error}</p>}
    </>}
    <label className="flex gap-2 items-center"><input type="checkbox" checked={consent}
      onChange={event => setConsent(event.target.checked)} />
      I authorize the reviewed reference repairs only.</label>
    <button className="button-primary" disabled={!state?.enabled || !consent || busy || active || complete || Boolean(blocked)}
      onClick={() => void start()}>{busy ? "Please wait…" : active ? "Backend repairs are active"
        : complete ? "Reference queue complete" : "Start backend reference repairs"}</button>
    {error && <p role="alert" className="text-danger">{error} No automatic resend will occur.</p>}
    <p>A verified receipt records a past correction. It does not prove that all Gila requirements now pass.</p>
  </Glass></AdminLayout>;
}
