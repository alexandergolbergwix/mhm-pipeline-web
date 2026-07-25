import {describe, expect, it} from "vitest";

import type {RunJobSnapshot} from "@/api/runJobs";
import {idsFingerprint, jobFingerprint} from "@/utils/renderStable";

function makeJob(overrides: Partial<RunJobSnapshot> = {}): RunJobSnapshot {
  return {
    id: "job-1",
    project_id: "proj-1",
    run_id: "run-1",
    kind: "ner_verify",
    status: "running",
    progress: {processed: 1, total: 10},
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

describe("idsFingerprint", () => {
  it("is equal for the same ids in the same order", () => {
    expect(idsFingerprint(["a", "b"])).toBe(idsFingerprint(["a", "b"]));
  });

  it("differs when order changes", () => {
    expect(idsFingerprint(["a", "b"])).not.toBe(idsFingerprint(["b", "a"]));
  });

  it("differs when membership changes", () => {
    expect(idsFingerprint(["a"])).not.toBe(idsFingerprint(["a", "b"]));
  });
});

describe("jobFingerprint", () => {
  it("is stable across object identity change with same content", () => {
    const a = makeJob();
    const b = makeJob({...a});
    expect(jobFingerprint(a)).toBe(jobFingerprint(b));
  });

  it("changes when progress updates", () => {
    const a = makeJob();
    const b = makeJob({progress: {processed: 2, total: 10}});
    expect(jobFingerprint(a)).not.toBe(jobFingerprint(b));
  });

  it("changes when a terminal result appears", () => {
    const running = makeJob({status: "succeeded", result: null});
    const done = makeJob({status: "succeeded", result: {outcomes: []}});
    expect(jobFingerprint(running)).not.toBe(jobFingerprint(done));
  });
});
