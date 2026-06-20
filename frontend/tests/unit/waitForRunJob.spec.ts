import {describe, expect, it, vi} from "vitest";

import {ApiError} from "@/api/client";
import {RunJobs, type RunJobSnapshot} from "@/api/runJobs";
import {
  loadStudioBuild,
  studioBuildJobIdFromConflict,
  waitForRunJob,
} from "@/utils/waitForRunJob";

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

describe("studioBuildJobIdFromConflict", () => {
  it("extracts job_id from structured 409 detail", () => {
    const detail = JSON.stringify({
      code: "studio_build_in_progress",
      message: "Wikidata Studio build is running in the background.",
      job_id: "e56425a1-8712-4605-b085-317261e678ed",
    });
    expect(studioBuildJobIdFromConflict(detail)).toBe(
      "e56425a1-8712-4605-b085-317261e678ed",
    );
  });
});

describe("loadStudioBuild", () => {
  it("waits for the job named in a 409 before retrying the fetch", async () => {
    const jobId = "e56425a1-8712-4605-b085-317261e678ed";
    const conflict = new ApiError(
      409,
      JSON.stringify({
        code: "studio_build_in_progress",
        message: "Wikidata Studio build is running in the background.",
        job_id: jobId,
      }),
    );
    const buildPayload = {items: [], summary: {total_items: 0}};
    const fetchBuild = vi
      .fn()
      .mockRejectedValueOnce(conflict)
      .mockResolvedValueOnce(buildPayload);
    const progress: string[] = [];

    vi.spyOn(RunJobs, "get").mockResolvedValue({
      id: jobId,
      project_id: "p1",
      run_id: "r1",
      kind: "wikidata_studio_build",
      status: "succeeded",
      progress: {message: "Built 120 items", processed: 120, total: 120},
      params: {},
      result: {},
      error: null,
      created_by: null,
      started_at: null,
      finished_at: null,
      cancel_requested_at: null,
      created_at: null,
      updated_at: null,
    });

    await expect(
      loadStudioBuild("r1", fetchBuild, {
        onProgress: (message) => { progress.push(message); },
      }),
    ).resolves.toBe(buildPayload);
    expect(fetchBuild).toHaveBeenCalledTimes(2);
    expect(progress.some((m) => m.includes("Built 120 items"))).toBe(true);
  });
});
