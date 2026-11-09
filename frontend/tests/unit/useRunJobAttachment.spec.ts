import {act, renderHook, waitFor} from "@testing-library/react";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";

import {RunJobs, type RunJobSnapshot} from "@/api/runJobs";
import {useRunJobAttachment} from "@/hooks/useRunJobAttachment";
import {useRunJobs} from "@/stores/runJobs";

function job(overrides: Partial<RunJobSnapshot> = {}): RunJobSnapshot {
  return {
    id: "j1", project_id: "p1", run_id: "r1", kind: "wikidata_upload", status: "running",
    progress: {}, params: {owner_only: true}, result: {outcomes: []}, error: null, created_by: "user-1",
    started_at: null, finished_at: null, cancel_requested_at: null, created_at: null, updated_at: null,
    ...overrides,
  };
}

const isNormalUpload = (snapshot: RunJobSnapshot) => snapshot.params.owner_only !== true;

beforeEach(() => {
  useRunJobs.getState().stopPolling();
  useRunJobs.setState({jobs: {}, pollTimer: null});
  vi.spyOn(RunJobs, "listMine").mockResolvedValue({jobs: []});
});

afterEach(() => {
  useRunJobs.getState().stopPolling();
  vi.restoreAllMocks();
});

describe("useRunJobAttachment", () => {
  it("does not attach an incompatible active job", async () => {
    vi.spyOn(RunJobs, "listForRun").mockResolvedValue({jobs: [job()]});
    const sync = vi.fn();
    const {result} = renderHook(() => useRunJobAttachment("r1", "wikidata_upload", sync, {acceptJob: isNormalUpload}));
    await waitFor(() => expect(RunJobs.listForRun).toHaveBeenCalled());
    expect(result.current.trackedJobId).toBeNull();
    expect(sync).not.toHaveBeenCalled();
  });

  it("delivers changed result contents when preview polling opts in", async () => {
    const sync = vi.fn();
    const first = job();
    vi.spyOn(RunJobs, "get").mockResolvedValue(first);
    const {result} = renderHook(() => useRunJobAttachment("r1", "wikidata_upload", sync, {autoAttach: false, includeResult: true}));
    act(() => {
      useRunJobs.getState().upsertJob(first);
      result.current.setTrackedJobId(first.id);
    });
    await waitFor(() => expect(sync).toHaveBeenCalledWith(first));
    const updated = job({result: {outcomes: [{status: "blocked", message: "reason"}]}});
    act(() => useRunJobs.getState().upsertJob(updated));
    await waitFor(() => expect(sync).toHaveBeenCalledWith(updated));
  });
});
