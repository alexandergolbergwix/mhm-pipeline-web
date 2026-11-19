import {useEffect, useState} from "react";

import type {ReferenceRepairStatus} from "@/api/referenceRepairs";

const israelTime = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Jerusalem", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});

function time(value: string | number | null | undefined) {
  const timestamp = typeof value === "string" ? Date.parse(value) : value;
  return timestamp != null && Number.isFinite(timestamp) ? israelTime.format(timestamp) : "Not available";
}

function duration(milliseconds: number) {
  const seconds = Math.max(0, Math.ceil(milliseconds / 1000));
  return seconds < 60 ? `${seconds}s` : seconds < 3600
    ? `${Math.floor(seconds / 60)}m ${seconds % 60}s`
    : `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
}

export function repairProgress(state: ReferenceRepairStatus) {
  const total = Math.max(0, Number.isFinite(state.total) ? Math.floor(state.total) : 0);
  const count = (value: number | undefined) => Math.min(total, Math.max(0,
    typeof value === "number" && Number.isFinite(value) ? Math.floor(value) : 0));
  const verified = count(state.verified);
  const clean = Math.min(total - verified, count(state.already_clean));
  // Only confirmed receipt counts contribute to the bar.
  const completed = verified + clean;
  return {total, verified, clean, completed, remaining: total - completed,
    percent: total > 0 ? completed / total * 100 : 0};
}

export function ReferenceRepairProgress({state, lastSync, connectionError}: {
  state: ReferenceRepairStatus; lastSync: number | null; connectionError: boolean;
}) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  const progress = repairProgress(state);
  const job = state.job;
  const phase = job?.progress.phase;
  const delayed = job?.status === "running" && (phase === "service_delay" || phase === "retry_wait");
  const queued = job?.status === "queued";
  const active = queued || job?.status === "running";
  const paused = job?.status === "failed" && (phase === "paused" || state.halted_status === "pre_send_retryable");
  const title = queued ? "In queue" : delayed ? "Wikidata delay" : job?.status === "running"
    ? phase === "start" ? "Start checks" : "Repairs in progress" : job?.status === "succeeded" ? "Queue complete"
      : paused ? "Queue paused" : job?.status === "failed" ? "Review required"
        : job?.status === "cancelled" ? "Queue cancelled" : "Ready to start";
  const description = queued ? job?.progress.message || "The job is saved. It will start when a worker is available."
    : delayed ? "Wikidata requested a delay. The backend will respect its saved retry time."
      : job?.status === "running" ? job.progress.message || "The backend will report each confirmed result."
        : paused ? "The checkpoint is saved. Resume will retain the same retry time."
          : "The counts below include confirmed results only.";
  const retry = job?.progress.next_attempt_at ? Date.parse(job.progress.next_attempt_at) : NaN;
  const probe = job?.progress.lag_probe_status;
  const probeDate = job?.progress.next_lag_probe_at;
  const nextProbe = probeDate ? Date.parse(probeDate) : NaN;
  const scan = job?.progress.mirror_scan;
  const lag = job?.progress.lag_probe_seconds;
  const waitsForLag = phase === "service_delay" && probe !== "ready";
  const retryMessage = retry > now ? `Next retry in ${duration(retry - now)}`
    : waitsForLag ? "The worker needs a successful lag check before it can retry."
      : "The worker can retry. Wait for the next update.";
  const elapsedStart = job?.started_at || job?.created_at;
  const elapsed = elapsedStart ? now - Date.parse(elapsedStart) : NaN;
  const workerDates = [state.worker_heartbeat_at, job?.status === "queued" ? null : job?.updated_at]
    .map(value => value ? Date.parse(value) : NaN).filter(Number.isFinite);
  const workerUpdate = workerDates.length ? Math.max(...workerDates) : null;
  const live = lastSync != null && now - lastSync < 10000 && !connectionError;

  return <div className="space-y-4 rounded-2xl border border-biu-sky/25 p-5" data-testid="reference-live-progress">
    <div className="flex flex-wrap justify-between items-start gap-3">
      <div><h2 className="text-xl font-semibold">{title}</h2><p className="text-sm muted mt-1">{description}</p></div>
      <span className="text-xs rounded-full border border-current px-3 py-1" role="status">
        {live ? "Live updates · every 2s" : connectionError ? "Connection issue" : "Status refresh"}
      </span>
    </div>
    <div className="flex justify-between text-sm gap-3"><strong>{progress.completed.toLocaleString()} / {progress.total.toLocaleString()} items complete</strong>
      <span>{Math.min(progress.remaining > 0 ? 99.9 : 100, progress.percent).toFixed(1)}%</span></div>
    <progress className="w-full h-3 accent-biu-sky" aria-label="Confirmed reference repair progress"
      max={Math.max(1, progress.total)} value={progress.completed} />
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
      {[["Verified corrections", progress.verified], ["Already clean", progress.clean], ["Items remaining", progress.remaining]].map(([label, value]) =>
        <div key={label} className="rounded-xl border border-white/15 p-3"><div className="text-2xl font-semibold">{typeof value === "number" ? value.toLocaleString() : value}</div><div className="text-sm muted">{label}</div></div>,
      )}
    </div>
    <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
      {job?.progress.current_qid && <p>Current item: <span className="font-mono">{job.progress.current_qid}</span></p>}
      {active && Number.isFinite(elapsed) && <p>{queued ? "Time in queue" : "Elapsed"}: {duration(elapsed)}</p>}
      {delayed && Number.isFinite(retry) && <p>{retryMessage}</p>}
    </div>
    {job?.status === "running" && progress.remaining > 0 && <p className="text-sm muted">
      {state.eta_seconds == null || !live
        ? "Completion estimate needs recent confirmed progress."
        : `Estimated time left is ${duration(state.eta_seconds * 1000)}. The estimate uses the last 25% of elapsed time.`}
    </p>}
    {(delayed || paused) && Number.isFinite(retry) && <p className="text-sm muted">
      Earliest edit retry: {time(retry)} Israel time.
    </p>}
    {delayed && phase === "service_delay" && <p className="text-sm muted">
      Read-only lag check: {probe === "ready" ? "Wikidata accepted the check"
        : probe === "delayed" ? "Wikidata still requests a delay"
          : probe === "unavailable" ? "No reliable result" : "No result yet"}.
      {probe === "delayed" && typeof lag === "number" && Number.isFinite(lag)
        && ` Reported lag: ${lag.toFixed(2)}. Edit limit: ${state.maxlag}.`}
      {Number.isFinite(nextProbe) && (nextProbe > now
        ? ` Next check in ${duration(nextProbe - now)} (${time(nextProbe)} Israel time).`
        : " The next check is due.")}
    </p>}
    {scan?.status && <div className="text-sm muted">
      <p>{scan.status === "unavailable" ? "The scan of saved snapshots could not finish."
        : `Saved snapshots: checked ${scan.reviewed ?? 0} of ${scan.total ?? 0} items.`}</p>
      {scan.reviewed != null && <p>{scan.candidates ?? 0} possible repairs · {scan.seen_clean ?? 0} appear clean · {scan.unchecked ?? 0} unavailable.</p>}
      <p>The scan does not verify public repairs.</p>
    </div>}
    <p className="text-xs muted">This bar covers the reference queue. It does not measure all Gila requirements.</p>
    <div className="flex flex-wrap justify-between gap-2 text-xs muted">
      <span>Last sync: {time(lastSync)} Israel time</span>
      <span>Last worker update: {workerUpdate == null ? "Not started" : `${time(workerUpdate)} Israel time`}</span>
    </div>
    {Boolean(state.recent_results?.length) && <details className="text-sm"><summary className="cursor-pointer">Recent confirmed results</summary>
      <ul className="mt-2 space-y-1">{state.recent_results?.map(row => <li key={row.qid}><span className="font-mono">{row.qid}</span> · {row.status === "verified" ? "Correction verified" : row.status === "already_clean" ? "Already clean" : "Review required"}</li>)}</ul>
    </details>}
  </div>;
}
