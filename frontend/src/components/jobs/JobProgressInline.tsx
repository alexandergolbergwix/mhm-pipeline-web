import type { RunJobSnapshot } from "@/api/runJobs";

interface JobProgressInlineProps {
  job: RunJobSnapshot;
  /** Verb shown per status, e.g. {running: "Uploading…", succeeded: "Upload complete:"}. */
  labels: {
    running: string;
    succeeded: string;
    failed: string;
    cancelled: string;
  };
}

/**
 * Inline progress line + bar for a background run job, rendered inside the
 * panel that started it (the JobTray shows the same job globally).
 */
export function JobProgressInline({ job, labels }: JobProgressInlineProps) {
  const { processed, total, message } = job.progress;
  const pct = total && total > 0 ? Math.round(((processed ?? 0) / total) * 100) : 0;
  const done = job.status === "succeeded" || job.status === "failed" || job.status === "cancelled";

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
          {message && <span className="text-ink">{message}</span>}
        </p>
        {!done && total ? (
          <span className="muted text-xs">{processed ?? 0} / {total}</span>
        ) : null}
      </div>
      {!done && (
        <div className="h-1.5 w-full rounded-full bg-white/8 overflow-hidden">
          <div
            className="h-full bg-biu-sky transition-[width] duration-300"
            style={{ width: `${Math.min(100, Math.max(pct, total ? 2 : 100))}%` }}
          />
        </div>
      )}
      {job.status === "failed" && job.error && (
        <p className="text-xs text-danger whitespace-pre-wrap">{job.error}</p>
      )}
    </div>
  );
}
