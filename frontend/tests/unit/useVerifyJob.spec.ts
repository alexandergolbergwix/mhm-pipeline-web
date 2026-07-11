import {act, renderHook, waitFor} from "@testing-library/react";
import {beforeEach, describe, expect, it, vi} from "vitest";

import {RunJobs, type RunJobSnapshot} from "@/api/runJobs";
import {useVerifyJob} from "@/hooks/useVerifyJob";
import {useRunJobs} from "@/stores/runJobs";

function verifyJob(overrides: Partial<RunJobSnapshot> = {}): RunJobSnapshot {
  return {
    id: "vj1",
    project_id: "p1",
    run_id: "r1",
    kind: "wikidata_verify",
    status: "running",
    progress: {processed: 0, total: 3, session_id: "sess-1"},
    params: {session_id: "sess-1"},
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

beforeEach(() => {
  useRunJobs.setState({jobs: {}, pollTimer: null});
  vi.restoreAllMocks();
});

describe("useVerifyJob", () => {
  it("upserts the job into the store when start() returns", async () => {
    const job = verifyJob();
    vi.spyOn(RunJobs, "start").mockResolvedValue(job);
    vi.spyOn(RunJobs, "listForRun").mockResolvedValue({jobs: []});
    const loadSession = vi.fn().mockResolvedValue(undefined);

    const {result} = renderHook(() => useVerifyJob({
      runId: "r1",
      kind: "wikidata_verify",
      loadSession,
    }));

    await act(async () => {
      await result.current.start({action_id: "audit_wikidata_item"});
    });

    await waitFor(() => {
      expect(useRunJobs.getState().jobs.vj1?.kind).toBe("wikidata_verify");
    });
  });

  it("clears running when the job enqueue request fails", async () => {
    vi.spyOn(RunJobs, "start").mockRejectedValue(new Error("request timed out"));
    vi.spyOn(RunJobs, "listForRun").mockResolvedValue({jobs: []});
    const loadSession = vi.fn().mockResolvedValue(undefined);

    const {result} = renderHook(() => useVerifyJob({
      runId: "r1",
      kind: "wikidata_verify",
      loadSession,
    }));

    await act(async () => {
      await expect(result.current.start({action_id: "audit_wikidata_item"}))
        .rejects.toThrow("request timed out");
    });

    expect(result.current.running).toBe(false);
  });

});
