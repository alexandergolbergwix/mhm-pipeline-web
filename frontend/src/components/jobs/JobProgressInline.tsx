import {useEffect, useState} from "react";
import type {RunJobSnapshot} from "@/api/runJobs";
import {
  JobStepsStrip,
  OVERALL_PROGRESS_TITLE,
  STEP_ONLY_TITLE,
} from "@/components/jobs/JobStepsStrip";
import {formatJobEta} from "@/utils/formatJobEta";
import type {BuildPhaseHint} from "@/lib/wikidataBuildPhases";

interface JobProgressInlineProps {
  job: RunJobSnapshot;
  /** Verb shown per status, e.g. {running: "Uploading…", succeeded: "Upload complete:"}. */
  labels: {
    running: string;
    succeeded: string;
    failed: string;
    cancelled: string;
  };
  /** Human guide per phase label (e.g. Wikidata Studio build phases): what
   *  the phase does and how long it usually takes. Matched on
   *  job.progress.phase; unknown phases render no hint. */
  phaseHints?: Record<string, BuildPhaseHint>;
}

function formatElapsed(ms: number): string {
  const minutes = Math.floor(ms / 60000);
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

/** Ticking elapsed readout while the job runs; null before start / when done. */
function useElapsed(startedAt: string | null, active: boolean): string | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active || !startedAt) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, [active, startedAt]);
  if (!active || !startedAt) return null;
  const start = Date.parse(startedAt);
  if (!Number.isFinite(start)) return null;
  return formatElapsed(Math.max(0, now - start));
}

/**
 * Inline progress line + bar for a background run job, rendered inside the
 * panel that started it (the JobTray shows the same job globally).
 */
export function JobProgressInline({job, labels, phaseHints}: JobProgressInlineProps) {
  const {
    processed,
    total,
    message,
    unit,
    sub_processed,
    sub_total,
    sub_unit,
    sub_message,
  } = job.progress;
  const steps = job.progress.steps;
  const hasSteps = Boolean(steps && steps.length > 0);
  const pct = total && total > 0 ? Math.round(((processed ?? 0) / total) * 100) : 0;
  const subPct =
    sub_total && sub_total > 0
      ? Math.round(((sub_processed ?? 0) / sub_total) * 100)
      : 0;
  const done = job.status === "succeeded" || job.status === "failed" || job.status === "cancelled";
  const unitSuffix = unit ? ` ${unit}` : "";
  const subUnitSuffix = sub_unit ? ` ${sub_unit}` : "";
  const showSub = !done && Boolean(sub_total && sub_total > 0);
  const elapsed = useElapsed(job.started_at, !done);
  const phaseHint = !done ? phaseHints?.[job.progress.phase ?? ""] : undefined;
  const stepEta = typeof job.progress.eta_seconds === "number"
    ? formatJobEta(job.progress.eta_seconds)
    : null;
  const allEta = typeof job.progress.process_eta_seconds === "number"
    ? formatJobEta(job.progress.process_eta_seconds)
    : null;

  return (
    <div className="border-t border-white/5 pt-3 space-y-2">
      <div className="flex items-baseline justify-between flex-wrap gap-2 text-sm">
        <p>
          <span className="muted">
            {job.status === "succeeded" ? labels.succeeded :
             job.status === "failed" ? labels.failed :
             job.status === "cancelled" ? labels.cancelled :
             labels.running}
          </span>{" "}
          {message && (
            <span className="text-ink" title={hasSteps ? STEP_ONLY_TITLE : undefined}>{message}</span>
          )}
        </p>
        {!done && total ? (
          <span className="muted text-xs" title={OVERALL_PROGRESS_TITLE}>
            {processed ?? 0} / {total}{unitSuffix}
            {hasSteps && <span className="ml-1 opacity-70">(overall)</span>}
            {elapsed && <span className="ml-2 opacity-80">· elapsed {elapsed}</span>}
          </span>
        ) : elapsed ? (
          <span className="muted text-xs">elapsed {elapsed}</span>
        ) : null}
      </div>
      {phaseHint && (
        <p className="muted text-xs">
          {phaseHint.detail} · typically {phaseHint.expected}
        </p>
      )}
      {!done && (stepEta || allEta) && (
        <p className="muted text-xs">
          {stepEta ? `This step: ${stepEta}` : ""}
          {stepEta && allEta ? " · " : ""}
          {allEta ? `Whole build: ${allEta}` : ""}
        </p>
      )}
      {hasSteps && <JobStepsStrip steps={steps ?? []} />}
      {!done && (
        <div className="h-1.5 w-full rounded-full bg-white/8 overflow-hidden">
          <div
            className="h-full bg-biu-sky transition-[width] duration-300"
            style={{width: `${Math.min(100, Math.max(pct, total ? 2 : 100))}%`}}
          />
        </div>
      )}
      {showSub && (
        <div className="space-y-1 pl-0.5">
          <div className="flex items-baseline justify-between flex-wrap gap-2 text-xs">
            <p className="muted truncate min-w-0">
              {sub_message || "Working…"}
            </p>
            <span className="muted shrink-0">
              {sub_processed ?? 0} / {sub_total}{subUnitSuffix}
            </span>
          </div>
          <div className="h-1 w-full rounded-full bg-white/8 overflow-hidden">
            <div
              className="h-full bg-biu-sky/70 transition-[width] duration-300"
              style={{width: `${Math.min(100, Math.max(subPct, 2))}%`}}
            />
          </div>
        </div>
      )}
      {job.status === "failed" && job.error && (
        <p className="text-xs text-danger whitespace-pre-wrap">{job.error}</p>
      )}
    </div>
  );
}
