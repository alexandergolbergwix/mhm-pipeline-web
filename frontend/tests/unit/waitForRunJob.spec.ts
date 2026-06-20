import {describe, expect, it, vi} from "vitest";

import {RunJobs, type RunJobSnapshot} from "@/api/runJobs";
import {waitForRunJob} from "@/utils/waitForRunJob";

describe("waitForRunJob", () => {
  it("resolves when the job succeeds", async () => {
    const job: RunJobSnapshot = {
      id: "j1",
      project_id: "p1",
      run_id: "r1",
      kind: "rdf_build",
      status: "succeeded",
      progress: {processed: 3, total: 3},
      params: {},
      result: {},
      error: null,
      created_by: null,
      started_at: null,
      finished_at: null,
      cancel_requested_at: null,
      created_at: null,
      updated_at: null,
    };
    vi.spyOn(RunJobs, "get").mockResolvedValue(job);
    await expect(waitForRunJob("r1", "j1", {timeoutMs: 1000})).resolves.toBe(job);
  });

  it("throws when the job fails", async () => {
    vi.spyOn(RunJobs, "get").mockResolvedValue({
      id: "j1",
      project_id: "p1",
      run_id: "r1",
      kind: "rdf_build",
      status: "failed",
      progress: {},
      params: {},
      result: null,
      error: "boom",
      created_by: null,
      started_at: null,
      finished_at: null,
      cancel_requested_at: null,
      created_at: null,
      updated_at: null,
    });
    await expect(waitForRunJob("r1", "j1", {timeoutMs: 1000})).rejects.toThrow("boom");
  });
});
