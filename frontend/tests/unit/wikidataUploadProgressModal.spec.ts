import {createElement} from "react";
import {describe, expect, it} from "vitest";
import {render, screen} from "@testing-library/react";

import type {RunJobSnapshot} from "@/api/runJobs";
import {resolveUploadTarget} from "@/components/wikidata/WikidataUploadProgressModal";
import {WikidataUploadSteps} from "@/components/wikidata/WikidataUploadSteps";
import {formatJobEta} from "@/utils/formatJobEta";

function job(overrides: Partial<RunJobSnapshot> = {}): RunJobSnapshot {
  return {
    id: "j1",
    project_id: "p1",
    run_id: "r1",
    kind: "wikidata_upload",
    status: "cancelled",
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
  };
}

describe("resolveUploadTarget", () => {
  it("prefers params.upload_target over a cancelled progress blob without it", () => {
    expect(resolveUploadTarget(job({
      params: {upload_target: "test"},
      progress: {phase: "cancelled", processed: 201, total: 233, message: "Cancelled by user"},
    }))).toBe("test");
  });

  it("uses sticky target when the job snapshot is briefly missing", () => {
    expect(resolveUploadTarget(null, "test")).toBe("test");
  });

  it("does not invent live when evidence is absent", () => {
    expect(resolveUploadTarget(null)).toBe("dry_run");
    expect(resolveUploadTarget(job())).toBe("dry_run");
  });
});

describe("formatJobEta", () => {
  it("hides until a number is known", () => {
    expect(formatJobEta(null)).toBe("Estimating…");
  });

  it("formats minutes", () => {
    expect(formatJobEta(240)).toBe("about 4 min left");
  });
});

describe("WikidataUploadSteps", () => {
  it("renders two labeled bars with Now and ETA on the running step", () => {
    render(createElement(WikidataUploadSteps, {progress: {
      processed: 1,
      total: 2,
      unit: "steps",
      elapsed_seconds: 12,
      eta_seconds: 240,
      current_label: "work · אב הרחמים",
      steps: [
        {
          id: "write_items",
          label: "Step 1 — Write items",
          status: "running",
          processed: 3,
          total: 10,
          unit: "items",
          eta_seconds: 240,
          current_label: "work · אב הרחמים",
        },
        {
          id: "add_connections",
          label: "Step 2 — Add connections",
          status: "pending",
          processed: 0,
          total: 4,
          unit: "links",
          eta_seconds: null,
          current_label: "Waiting for step 1",
        },
      ],
    }}));
    expect(screen.getByTestId("wikidata-upload-step-1")).toHaveAttribute("data-status", "running");
    expect(screen.getByTestId("wikidata-upload-step-1-now")).toHaveTextContent("work · אב הרחמים");
    expect(screen.getByTestId("wikidata-upload-step-1-eta")).toHaveTextContent("about 4 min left");
    expect(screen.getByTestId("wikidata-upload-step-2")).toHaveAttribute("data-status", "pending");
    expect(screen.getByTestId("wikidata-upload-step-2-now")).toHaveTextContent("Waiting for step 1");
    expect(screen.getByTestId("wikidata-upload-step-2-eta")).toHaveTextContent("Time: —");
  });

  it("renders the same two step bars for a live-target progress blob", () => {
    render(createElement(WikidataUploadSteps, {progress: {
      upload_target: "live",
      processed: 1,
      total: 2,
      unit: "steps",
      current_label: "work · MS Alpha",
      steps: [
        {
          id: "write_items",
          label: "Step 1 — Write items",
          status: "running",
          processed: 2,
          total: 8,
          unit: "items",
          current_label: "work · MS Alpha",
        },
        {
          id: "add_connections",
          label: "Step 2 — Add connections",
          status: "pending",
          processed: 0,
          total: 3,
          unit: "links",
          current_label: "Waiting for step 1",
        },
      ],
    }}));
    expect(screen.getByTestId("wikidata-upload-steps")).toBeInTheDocument();
    expect(screen.getByTestId("wikidata-upload-step-1")).toHaveAttribute("data-status", "running");
    expect(screen.getByTestId("wikidata-upload-step-2")).toHaveAttribute("data-status", "pending");
    expect(screen.getByTestId("wikidata-upload-step-1-now")).toHaveTextContent("work · MS Alpha");
  });
});
