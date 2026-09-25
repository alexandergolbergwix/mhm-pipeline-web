/**
 * JobTray — floating card parity with the inline progress widget.
 *
 * For jobs that report a `steps[]` plan (HMO item upload):
 *  • the label line shows the overall counter labeled "(overall)" with a
 *    tooltip, plus the step-local message with its own tooltip;
 *  • the per-step strip renders above the overall bar.
 *
 * Jobs without `steps[]` and `wikidata_upload` (own two-step widget) render
 * exactly as before.
 */

import {beforeEach, describe, expect, it} from "vitest";
import {render, screen} from "@testing-library/react";
import {MemoryRouter} from "react-router-dom";

import type {RunJobSnapshot} from "@/api/runJobs";
import {JobTray} from "@/components/jobs/JobTray";
import {
  OVERALL_PROGRESS_TITLE,
  STEP_ONLY_TITLE,
} from "@/components/jobs/JobStepsStrip";
import {useRunJobs} from "@/stores/runJobs";

const UPLOAD_STEPS = [
  {id: "upload_items", label: "Upload items", status: "done", processed: 18512, total: 18512, unit: "items"},
  {id: "add_links", label: "Add item links", status: "running", processed: 37523, total: 55623, unit: "links"},
];

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
    ...overrides,
  } as RunJobSnapshot;
}

function renderTray(jobSnapshots: RunJobSnapshot[]) {
  useRunJobs.setState({
    jobs: Object.fromEntries(jobSnapshots.map((j) => [j.id, j])),
  });
  return render(
    <MemoryRouter>
      <JobTray />
    </MemoryRouter>,
  );
}

describe("<JobTray>", () => {
  beforeEach(() => {
    useRunJobs.setState({jobs: {}});
  });

  it("shows the overall-labeled counter, the step message tooltip, and the steps strip", () => {
    const j = job();
    j.progress = {
      processed: 56035,
      total: 74135,
      message: "37523/55623 item links added",
      steps: UPLOAD_STEPS,
    };
    renderTray([j]);

    const overall = screen.getByText(/56035 \/ 74135/);
    expect(overall.textContent).toContain("(overall)");
    expect(overall.getAttribute("title")).toBe(OVERALL_PROGRESS_TITLE);
    expect(screen.getByText(/37523\/55623 item links added/).getAttribute("title")).toBe(
      STEP_ONLY_TITLE,
    );
    const strip = screen.getByTestId("job-progress-steps");
    expect(strip.textContent).toContain("Upload items");
    expect(strip.textContent).toContain("18512/18512 items");
    expect(strip.textContent).toContain("Add item links");
    expect(strip.textContent).toContain("37523/55623 links");
  });

  it("keeps the wikidata_upload two-step widget without a duplicate strip", () => {
    const j = job({kind: "wikidata_upload"});
    j.progress = {
      processed: 1,
      total: 2,
      unit: "steps",
      message: "Step 1 of 2: writing items",
      steps: UPLOAD_STEPS,
    };
    renderTray([j]);

    expect(screen.queryByTestId("job-progress-steps")).toBeNull();
    expect(screen.getByTestId("wikidata-upload-steps")).toBeInTheDocument();
  });

  it("keeps the plain label for jobs without a steps plan", () => {
    const j = job({kind: "rdf_build"});
    j.progress = {processed: 3, total: 5, message: "3/5 items uploaded"};
    renderTray([j]);

    expect(screen.queryByTestId("job-progress-steps")).toBeNull();
    expect(screen.getByText(/3 \/ 5/).textContent).not.toContain("(overall)");
  });
});
