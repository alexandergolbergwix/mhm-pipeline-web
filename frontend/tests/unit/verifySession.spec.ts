import {describe, expect, it} from "vitest";

import type {RunJobSnapshot} from "@/api/runJobs";
import {shouldLoadVerifySession} from "@/utils/verifySession";

function job(partial: Partial<RunJobSnapshot>): RunJobSnapshot {
  return {
    id: "j1",
    project_id: "p1",
    run_id: "r1",
    kind: "authority_verify",
    status: "queued",
    progress: {},
    params: {session_id: "sess-1"},
    result: null,
    error: null,
    created_by: null,
    started_at: null,
    finished_at: null,
    cancel_requested_at: null,
    created_at: null,
    updated_at: null,
    ...partial,
  };
}

describe("shouldLoadVerifySession", () => {
  it("skips load while queued with no progress", () => {
    expect(shouldLoadVerifySession(job({status: "queued"}))).toBe(false);
  });

  it("loads once the worker reports progress", () => {
    expect(shouldLoadVerifySession(job({
      status: "running",
      progress: {session_id: "sess-1", processed: 3, total: 10},
    }))).toBe(true);
  });

  it("loads when the job has finished", () => {
    expect(shouldLoadVerifySession(job({status: "succeeded"}))).toBe(true);
  });
});
