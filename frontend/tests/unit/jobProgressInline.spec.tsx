/**
 * JobProgressInline — steps strip + scale labeling.
 *
 * Jobs that report a `steps[]` plan (e.g. the two-pass HMO item upload)
 * must make the two number scales readable:
 *
 *  • the step-local `message` ("N/M item links added") gets a tooltip
 *    saying it counts the current step only;
 *  • the right-hand counter is labeled "(overall)" with its own tooltip;
 *  • a per-step strip shows every step with status + n/m counters.
 *
 * Jobs without `steps[]` render exactly as before (no strip, no label).
 */

import {describe, expect, it} from "vitest";
import {render, screen} from "@testing-library/react";

import type {RunJobSnapshot} from "@/api/runJobs";
import {JobProgressInline} from "@/components/jobs/JobProgressInline";

const LABELS = {
  running: "Uploading…",
  succeeded: "Upload complete:",
  failed: "Upload failed:",
  cancelled: "Upload cancelled:",
};

const OVERALL_TITLE = "Overall progress — all steps combined (one tick per write).";

function job(overrides: Partial<RunJobSnapshot> = {}): RunJobSnapshot {
  return {
    id: "job-1",
    run_id: "run-9",
    kind: "hmo_item_upload",
    status: "running",
    progress: {},
    params: {},
    result: null,
    error: null,
    created_by: null,
    started_at: null,
    finished_at: null,
    cancel_requested_at: null,
    created_at: null,
    updated_at: null,
  } as RunJobSnapshot;
}

const UPLOAD_STEPS = [
  {id: "upload_items", label: "Upload items", status: "done", processed: 18512, total: 18512, unit: "items"},
  {id: "add_links", label: "Add item links", status: "running", processed: 18695, total: 55623, unit: "links"},
];

describe("<JobProgressInline>", () => {
  it("renders the steps strip with per-step counters and marks the running step", () => {
    const j = job();
    j.progress = {
      processed: 37207,
      total: 74135,
      message: "18695/55623 item links added",
      steps: UPLOAD_STEPS,
    };
    render(<JobProgressInline job={j} labels={LABELS} />);

    const strip = screen.getByTestId("job-progress-steps");
    expect(strip.textContent).toContain("Upload items");
    expect(strip.textContent).toContain("18512/18512 items");
    expect(strip.textContent).toContain("Add item links");
    expect(strip.textContent).toContain("18695/55623 links");
    const runningStep = strip.querySelector('[data-status="running"]');
    expect(runningStep?.textContent).toContain("Add item links");
    expect(runningStep?.getAttribute("title")).toMatch(/this step only/i);
  });

  it("labels the right-hand counter as overall and explains the step-local message", () => {
    const j = job();
    j.progress = {
      processed: 37207,
      total: 74135,
      message: "18695/55623 item links added",
      steps: UPLOAD_STEPS,
    };
    render(<JobProgressInline job={j} labels={LABELS} />);

    const overall = screen.getByText(/37207 \/ 74135/);
    expect(overall.textContent).toContain("(overall)");
    expect(overall.getAttribute("title")).toMatch(/all steps combined/i);
    expect(screen.getByText("18695/55623 item links added").getAttribute("title")).toMatch(
      /current step only/i,
    );
  });

  it("renders nothing extra for jobs without a steps plan", () => {
    const j = job();
    j.progress = {processed: 3, total: 5, message: "3/5 items uploaded"};
    render(<JobProgressInline job={j} labels={LABELS} />);

    expect(screen.queryByTestId("job-progress-steps")).toBeNull();
    expect(screen.getByText(/3 \/ 5/).getAttribute("title")).toBe(OVERALL_TITLE);
    expect(screen.getByText("3/5 items uploaded").getAttribute("title")).toBeNull();
  });

  it("shows completed steps after the job succeeds", () => {
    const j = job();
    j.status = "succeeded";
    j.progress = {
      processed: 74135,
      total: 74135,
      message: "Upload complete",
      steps: UPLOAD_STEPS.map((s) => ({...s, status: "done"})),
    };
    render(<JobProgressInline job={j} labels={LABELS} />);

    const strip = screen.getByTestId("job-progress-steps");
    expect(strip.querySelectorAll('[data-status="done"]').length).toBe(2);
    // The overall counter + bar hide once done; the strip stays for closure.
    expect(screen.queryByText(/overall/)).toBeNull();
  });
});

