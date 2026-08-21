import type {RunJobProgress} from "@/api/runJobs";
import {formatJobElapsed, formatJobEta} from "@/utils/formatJobEta";

export interface WikidataUploadStepsProps {
  progress: RunJobProgress;
}

const DEFAULT_STEPS = [
  {id: "write_items", label: "Step 1 — Write items", unit: "items"},
  {id: "add_connections", label: "Step 2 — Add connections", unit: "links"},
];

function statusLabel(status: string | undefined, index: number): string {
  if (status === "running") return "In progress";
  if (status === "done") return "Done";
  if (status === "skipped") return "Skipped";
  return index === 0 ? "Waiting" : "Waiting for step 1";
}

export function WikidataUploadSteps({progress}: WikidataUploadStepsProps) {
  const steps = (progress.steps && progress.steps.length >= 2)
    ? progress.steps
    : DEFAULT_STEPS.map((meta, idx) => ({
      ...meta,
      status: (progress.processed ?? 1) > idx + 1
        ? "done"
        : (progress.processed ?? 1) === idx + 1 ? "running" : "pending",
      processed: idx === 0 ? (progress.sub_processed ?? 0) : 0,
      total: idx === 0 ? (progress.sub_total ?? 0) : 0,
      eta_seconds: idx === 0 ? progress.eta_seconds : null,
      current_label: idx === 0 ? (progress.current_label || progress.sub_message) : "Waiting for step 1",
    }));

  return (
    <div className="space-y-3" data-testid="wikidata-upload-steps">
      {steps.map((step, idx) => {
        const total = Number(step.total ?? 0);
        const processed = Number(step.processed ?? 0);
        const pct = total > 0 ? Math.round((processed / total) * 100) : (
          step.status === "done" ? 100 : 0
        );
        const running = step.status === "running";
        const nowText = running
          ? (step.current_label || progress.current_label || "Working…")
          : (step.current_label || statusLabel(step.status, idx));
        const unit = step.unit ? ` ${step.unit}` : "";
        const eta = running
          ? formatJobEta(step.eta_seconds ?? progress.eta_seconds)
          : (step.status === "pending" ? "—" : (step.status === "done" ? "Done" : "—"));
        const elapsed = running
          ? formatJobElapsed(progress.elapsed_seconds)
          : "";

        return (
          <div
            key={step.id || String(idx)}
            className="space-y-1.5"
            data-testid={`wikidata-upload-step-${idx + 1}`}
            data-status={step.status || "pending"}
          >
            <div className="flex items-baseline justify-between gap-2">
              <p className="text-sm font-medium text-ink">
                {step.label || DEFAULT_STEPS[idx]?.label}
              </p>
              <span className="muted text-xs shrink-0">
                {statusLabel(step.status, idx)}
                {total > 0 ? ` · ${processed} / ${total}${unit}` : ""}
              </span>
            </div>
            <div className="h-1.5 w-full rounded-full bg-white/8 overflow-hidden">
              <div
                className="h-full bg-biu-sky transition-[width] duration-300"
                style={{width: `${Math.min(100, Math.max(pct, running && total ? 2 : pct))}%`}}
              />
            </div>
            <p className="text-xs muted truncate" data-testid={`wikidata-upload-step-${idx + 1}-now`}>
              Now: {nowText}
            </p>
            <p className="text-xs muted" data-testid={`wikidata-upload-step-${idx + 1}-eta`}>
              Time: {elapsed ? `${elapsed} · ${eta}` : eta}
            </p>
          </div>
        );
      })}
    </div>
  );
}
