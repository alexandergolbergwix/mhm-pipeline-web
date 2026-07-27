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

  it("reports incomplete scope without blaming a generic eval-agent error", async () => {
    const onFailed = vi.fn();
    const loadSession = vi.fn().mockResolvedValue(undefined);
    vi.spyOn(RunJobs, "listForRun").mockResolvedValue({jobs: []});
    const running = verifyJob({status: "running"});
    const done = verifyJob({
      status: "succeeded",
      result: {
        judged: 54,
        total: 313,
        outcome: "partial",
        resumable: true,
        session_id: "sess-1",
      },
      progress: {processed: 54, total: 313, session_id: "sess-1"},
    });
    vi.spyOn(RunJobs, "start").mockResolvedValue(running);
    vi.spyOn(RunJobs, "get").mockResolvedValue(done);

    const {result} = renderHook(() => useVerifyJob({
      runId: "r1",
      kind: "wikidata_verify",
      loadSession,
      onFailed,
    }));

    await act(async () => {
      await result.current.start({action_id: "audit_wikidata_item"});
    });

    await waitFor(() => {
      expect(onFailed).toHaveBeenCalled();
    });
    expect(String(onFailed.mock.calls[0]?.[0])).toContain("Continue");
    expect(String(onFailed.mock.calls[0]?.[0])).not.toContain("eval-agent error");
    expect(result.current.resumeOffer?.judged).toBe(54);
  });

  it("continueFromPause starts a new job with override_cache false", async () => {
    const loadSession = vi.fn().mockResolvedValue(undefined);
    const interrupted = verifyJob({
      status: "failed",
      result: {
        resumable: true,
        judged: 61,
        total: 313,
        outcome: "partial",
        session_id: "sess-1",
      },
      params: {
        session_id: "sess-1",
        action_id: "audit_wikidata_item",
        item_ids: ["a", "b"],
        override_cache: true,
        source: "canonical",
      },
      error: "Verification interrupted after 61 of 313. Cached verdicts were kept — click Continue to resume the remaining items.",
    });
    vi.spyOn(RunJobs, "listForRun").mockResolvedValue({jobs: [interrupted]});
    const startSpy = vi.spyOn(RunJobs, "start").mockResolvedValue(
      verifyJob({id: "vj2", status: "queued"}),
    );

    const {result} = renderHook(() => useVerifyJob({
      runId: "r1",
      kind: "wikidata_verify",
      loadSession,
    }));

    await waitFor(() => {
      expect(result.current.resumeOffer?.judged).toBe(61);
    });

    await act(async () => {
      await result.current.continueFromPause({tier_model: "gemini-3.5-flash"});
    });

    expect(startSpy).toHaveBeenCalled();
    const params = startSpy.mock.calls[0]?.[2] as Record<string, unknown>;
    expect(params.override_cache).toBe(false);
    expect(params.action_id).toBe("audit_wikidata_item");
    expect(params.session_id).toBeUndefined();
  });

});
