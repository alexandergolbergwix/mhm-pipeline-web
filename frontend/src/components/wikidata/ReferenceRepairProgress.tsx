import {useEffect, useState, type ReactNode} from "react";

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

export function ReferenceRepairProgress({state, lastSync, connectionError, children}: {
  state: ReferenceRepairStatus; lastSync: number | null; connectionError: boolean; children?: ReactNode;
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
    : delayed ? "The worker will retry when Wikidata permits edits."
      : job?.status === "running" ? job.progress.message
        : paused ? "The worker saved its place in the queue." : null;
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
  const statusColor = delayed || paused ? "text-warn" : job?.status === "failed" ? "text-danger"
    : job?.status === "succeeded" ? "text-success" : active ? "text-biu-sky" : "muted";
  const showElapsed = active && Number.isFinite(elapsed);
  const percent = Math.min(progress.remaining > 0 ? 99.9 : 100, progress.percent).toFixed(1);
  const stats = [
    {label: "Verified corrections", value: progress.verified},
    {label: "Already clean", value: progress.clean},
    {label: "Items remaining", value: progress.remaining},
  ];

  return <>
    <section aria-labelledby="reference-queue-state" data-testid="reference-live-progress"
      className="rounded-2xl border border-glass-line bg-[var(--surface-inset)]">
      <div className="space-y-5 p-4 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <h2 id="reference-queue-state" className={`flex items-center gap-2 text-lg font-semibold ${statusColor}`}>
            <span aria-hidden="true" className="size-2 shrink-0 rounded-full bg-current" />
            {title}
          </h2>
          <span className={`text-xs ${connectionError ? "text-danger" : "muted"}`} role="status">
            {live ? "Live updates" : connectionError ? "Connection issue" : "Status refresh"}
          </span>
          {description && <p className="w-full text-sm muted">{description}</p>}
        </div>

        <div className="space-y-3">
          <div className="flex items-baseline justify-between gap-3 tabular-nums">
            <p className="flex flex-wrap items-baseline gap-x-2">
              <strong className="text-2xl font-semibold sm:text-4xl">{progress.completed.toLocaleString()}</strong>
              <span className="text-sm muted">/ {progress.total.toLocaleString()} items complete</span>
            </p>
            <strong className="text-sm font-semibold text-biu-sky">{percent}%</strong>
          </div>
          <progress className="reference-repair-bar" aria-label="Confirmed reference repair progress"
            max={Math.max(1, progress.total)} value={progress.completed} />
          <dl className="grid grid-cols-3 divide-x divide-glass-line pt-2">
            {stats.map(({label, value}) => <div key={label} className="flex flex-col px-2 first:pl-0 last:pr-0 sm:px-4">
              <dt className="order-2 text-xs muted sm:text-sm">{label}</dt>
              <dd className="text-xl font-semibold tabular-nums sm:text-2xl">{value.toLocaleString()}</dd>
            </div>)}
          </dl>
        </div>

        {(job?.progress.current_qid || showElapsed || (job?.status === "running" && progress.remaining > 0)) &&
          <div className="flex flex-wrap gap-x-5 gap-y-2 border-t border-glass-line pt-4 text-sm muted">
            {job?.progress.current_qid && <p>Current item <span className="font-mono text-ink">{job.progress.current_qid}</span></p>}
            {showElapsed && <p>{queued ? "Time in queue" : "Elapsed"} <span className="tabular-nums text-ink">{duration(elapsed)}</span></p>}
            {job?.status === "running" && progress.remaining > 0 && <p className={state.eta_seconds != null && live ? "font-medium text-ink" : ""}>
              {state.eta_seconds == null || !live
                ? "The estimate needs recent confirmed progress."
                : `Estimated time left is ${duration(state.eta_seconds * 1000)}.`}
            </p>}
          </div>}

        {(delayed || (paused && Number.isFinite(retry))) &&
          <div className="space-y-1 rounded-xl bg-[var(--warn-bg)] px-4 py-3 text-sm">
            {delayed && (Number.isFinite(retry) || waitsForLag) && <p className="font-semibold text-warn">{retryMessage}</p>}
            {Number.isFinite(retry) && <p className="muted">Earliest edit retry: {time(retry)} Israel time.</p>}
            {delayed && phase === "service_delay" && <p className="muted">
              Read-only lag check: {probe === "ready" ? "Wikidata accepted the check"
                : probe === "delayed" ? "Wikidata still requests a delay"
                  : probe === "unavailable" ? "No reliable result" : "No result yet"}.
              {probe === "delayed" && typeof lag === "number" && Number.isFinite(lag)
                && ` Reported lag: ${lag.toFixed(2)}. Edit limit: ${state.maxlag}.`}
              {Number.isFinite(nextProbe) && (nextProbe > now
                ? ` Next check in ${duration(nextProbe - now)} (${time(nextProbe)} Israel time).`
                : " The next check is due.")}
            </p>}
          </div>}
      </div>
      {children}
    </section>
    <div className="divide-y divide-glass-line text-sm">
      {Boolean(state.recent_results?.length) && <details className="py-3">
        <summary className="cursor-pointer font-medium">Recent confirmed results</summary>
        <ul className="mt-3 space-y-2 muted">{state.recent_results?.map(row => <li key={row.qid} className="flex flex-wrap gap-x-3">
          <span className="font-mono text-ink">{row.qid}</span>
          <span>{row.status === "verified" ? "Correction verified" : row.status === "already_clean" ? "Already clean" : "Review required"}</span>
        </li>)}</ul>
      </details>}
      <details className="py-3">
        <summary className="cursor-pointer font-medium">Queue details</summary>
        <div className="mt-3 space-y-3 muted">
          <dl className="flex flex-wrap gap-x-6 gap-y-2">
            <div><dt>Last sync</dt><dd className="tabular-nums text-ink">{lastSync == null ? "Not available" : `${time(lastSync)} Israel time`}</dd></div>
            <div><dt>Last worker update</dt><dd className="tabular-nums text-ink">{workerUpdate == null ? "Not started" : `${time(workerUpdate)} Israel time`}</dd></div>
          </dl>
          {scan?.status && <div>
            <p>{scan.status === "unavailable" ? "The scan of saved snapshots could not finish."
              : `Saved snapshots: checked ${scan.reviewed ?? 0} of ${scan.total ?? 0} items.`}</p>
            {scan.reviewed != null && <p>{scan.candidates ?? 0} possible repairs · {scan.seen_clean ?? 0} appear clean · {scan.unchecked ?? 0} unavailable.</p>}
            <p>The scan does not verify public repairs.</p>
          </div>}
        </div>
      </details>
    </div>
  </>;
}
