import {useEffect, useMemo} from "react";
import {Link} from "react-router-dom";

import {
  JOB_KIND_LABELS,
  jobRunHref,
  type RunJobSnapshot,
} from "@/api/runJobs";
import {Glass, GlassPill} from "@/components/glass";
import {isJobActive, useRunJobs} from "@/stores/runJobs";
import {formatJobEtaShort} from "@/utils/formatJobEta";

const notifiedRef = {current: new Set<string>()};

function maybeNotify(job: RunJobSnapshot) {
  if (typeof document === "undefined" || document.hidden !== true) return;
  if (job.status !== "succeeded" && job.status !== "failed") return;
  if (notifiedRef.current.has(job.id)) return;
  notifiedRef.current.add(job.id);
  const label = JOB_KIND_LABELS[job.kind] ?? job.kind;
  if (typeof Notification !== "undefined" && Notification.permission === "granted") {
    new Notification(label, {
      body: job.status === "succeeded" ? "Completed" : (job.error ?? "Failed"),
    });
  }
}

function progressLabel(job: RunJobSnapshot): string {
  const p = job.progress ?? {};
  const total = Number(p.total ?? 0);
  const processed = Number(p.processed ?? 0);
  const msg = typeof p.message === "string" ? p.message.trim() : "";
  const unit = typeof p.unit === "string" && p.unit.trim() ? ` ${p.unit.trim()}` : "";
  const subTotal = Number(p.sub_total ?? 0);
  const subProcessed = Number(p.sub_processed ?? 0);
  const subUnit = typeof p.sub_unit === "string" && p.sub_unit.trim()
    ? ` ${p.sub_unit.trim()}`
    : "";
  const subMsg = typeof p.sub_message === "string" ? p.sub_message.trim() : "";
  if (total > 0) {
    const frac = `${processed} / ${total}${unit}`;
    if (subTotal > 0) {
      const sub = `${subProcessed} / ${subTotal}${subUnit}`;
      const detail = subMsg || msg;
      return detail ? `${frac} · ${sub} · ${detail}` : `${frac} · ${sub}`;
    }
    return msg ? `${frac} · ${msg}` : frac;
  }
  return msg || job.status;
}

function trayEtaSuffix(job: RunJobSnapshot): string {
  const short = formatJobEtaShort(job.progress?.eta_seconds);
  return short ? ` · ${short}` : "";
}

export function JobTray() {
  const jobsRecord = useRunJobs((s) => s.jobs);
  const jobs = useMemo(
    () => Object.values(jobsRecord).filter((j) => isJobActive(j.status)),
    [jobsRecord],
  );
  const cancelJob = useRunJobs((s) => s.cancelJob);

  if (jobs.length === 0) return null;

  return (
    <div
      className="fixed bottom-4 right-4 z-[60] flex flex-col gap-2 max-w-sm w-[min(100vw-2rem,22rem)]"
      data-testid="job-tray"
    >
      {jobs.map((job) => {
        const total = Number(job.progress?.total ?? 0);
        const processed = Number(job.progress?.processed ?? 0);
        const pct = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;
        const subTotal = Number(job.progress?.sub_total ?? 0);
        const subProcessed = Number(job.progress?.sub_processed ?? 0);
        const subPct =
          subTotal > 0 ? Math.min(100, Math.round((subProcessed / subTotal) * 100)) : 0;
        const label = JOB_KIND_LABELS[job.kind] ?? job.kind;

        return (
          <Glass key={job.id} variant="compact" className="p-3 space-y-2 shadow-lg">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="text-sm font-medium truncate">{label}</p>
                <p className="text-xs muted truncate">{progressLabel(job)}{trayEtaSuffix(job)}</p>
              </div>
              <GlassPill className="px-2 py-0.5 text-[10px] uppercase tracking-wide shrink-0">
                {job.status}
              </GlassPill>
            </div>
            {total > 0 && (
              <div className="h-1.5 rounded-full bg-black/10 overflow-hidden" aria-hidden>
                <div
                  className="h-full bg-[var(--accent)] transition-all duration-300"
                  style={{width: `${pct}%`}}
                />
              </div>
            )}
            {subTotal > 0 && (
              <div className="h-1 rounded-full bg-black/10 overflow-hidden" aria-hidden>
                <div
                  className="h-full bg-[var(--accent)]/70 transition-all duration-300"
                  style={{width: `${Math.max(2, subPct)}%`}}
                />
              </div>
            )}
            <div className="flex items-center gap-2 justify-end">
              <Link to={jobRunHref(job)} className="button-ghost text-xs !py-1">
                View
              </Link>
              {isJobActive(job.status) && (
                <button
                  type="button"
                  className="button-ghost text-xs !py-1 text-warn"
                  data-testid={`job-cancel-${job.id}`}
                  onClick={() => { void cancelJob(job.run_id, job.id); }}
                >
                  Cancel
                </button>
              )}
            </div>
          </Glass>
        );
      })}
    </div>
  );
}

export function JobTrayBootstrap() {
  const ensurePolling = useRunJobs((s) => s.ensurePolling);
  const refresh = useRunJobs((s) => s.refresh);
  const stopPolling = useRunJobs((s) => s.stopPolling);
  const jobsRecord = useRunJobs((s) => s.jobs);
  const jobs = useMemo(() => Object.values(jobsRecord), [jobsRecord]);

  useEffect(() => {
    if (typeof Notification !== "undefined" && Notification.permission === "default") {
      void Notification.requestPermission();
    }
  }, []);

  useEffect(() => {
    for (const job of jobs) maybeNotify(job);
  }, [jobs]);

  useEffect(() => {
    void refresh().then(() => ensurePolling());
    return () => stopPolling();
  }, [ensurePolling, refresh, stopPolling]);

  return <JobTray />;
}
