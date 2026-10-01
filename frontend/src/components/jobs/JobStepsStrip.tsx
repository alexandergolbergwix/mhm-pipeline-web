import {formatJobEtaShort} from "@/utils/formatJobEta";
import type {RunJobSnapshot} from "@/api/runJobs";

export type ProgressStep = NonNullable<RunJobSnapshot["progress"]["steps"]>[number];

/** Tooltip scale explanations shared by the inline widget and the job tray (frontend R15). */
export const OVERALL_PROGRESS_TITLE =
  "Overall progress — all steps combined (one tick per write).";
export const STEP_ONLY_TITLE =
  "Counts the current step only. The number on the right and the bar cover all steps combined.";
const STEP_CHIP_TITLE = "Counts this step only.";

/**
 * Per-step strip for jobs that report a `steps[]` plan (e.g. the two-pass
 * HMO item upload: pass 1 uploads items, pass 2 adds the item links).
 * Sits above the overall bar so the step-local numbers in `message` never
 * read as the same scale as the overall counter.
 */
export function JobStepsStrip({steps}: {steps: ProgressStep[]}) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs" data-testid="job-progress-steps">
      {steps.map((step, i) => {
        const status = step.status ?? "pending";
        const total = Number(step.total ?? 0);
        const processed = Number(step.processed ?? 0);
        const pass = status === "running" && step.current_label
          ? ` · ${step.current_label}`
          : "";
        const eta = status === "running" && typeof step.eta_seconds === "number"
          ? formatJobEtaShort(step.eta_seconds)
          : null;
        const counts = total > 0 ? ` · ${processed}/${total}${step.unit ? ` ${step.unit}` : ""}` : "";
        const pct = total > 0 ? Math.min(100, Math.max(2, Math.round((processed / total) * 100))) : 0;
        const tone =
          status === "running" ? "text-biu-sky font-medium"
            : status === "skipped" ? "text-warn"
              : "muted";
        const marker = status === "done" ? "✓" : status === "running" ? "●" : status === "skipped" ? "–" : "○";
        return (
          <span
            key={step.id ?? i}
            className={`inline-flex flex-col gap-0.5 min-w-[8rem] ${tone}`}
            data-status={status}
            title={STEP_CHIP_TITLE}
          >
            <span className="inline-flex items-center gap-1">
              <span aria-hidden>{marker}</span>
              <span>{step.label ?? `Step ${i + 1}`}{pass}{counts}{eta ? ` · ${eta}` : ""}</span>
            </span>
            {step.description ? (
              <span className="muted pl-4">{step.description}</span>
            ) : null}
            {status === "running" && (
              <span className="block h-0.5 w-full rounded-full bg-white/8 overflow-hidden">
                {total > 0 ? (
                  <span
                    className="block h-full bg-biu-sky transition-[width] duration-300"
                    style={{width: `${pct}%`}}
                  />
                ) : (
                  // CPU-bound phase with no per-record callback: show alive,
                  // stay honest about not knowing the fraction.
                  <span className="block h-full w-1/3 bg-biu-sky/70 animate-pulse" />
                )}
              </span>
            )}
          </span>
        );
      })}
    </div>
  );
}
