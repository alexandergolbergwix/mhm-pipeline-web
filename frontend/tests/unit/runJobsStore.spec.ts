import {beforeEach, describe, expect, it, vi} from "vitest";

import {RunJobs, type RunJobSnapshot} from "@/api/runJobs";
import {useRunJobs} from "@/stores/runJobs";

function job(overrides: Partial<RunJobSnapshot> = {}): RunJobSnapshot {
  return {
    id: "j1",
    project_id: "p1",
    run_id: "r1",
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
  };
}

beforeEach(() => {
  useRunJobs.setState({jobs: {}, pollTimer: null});
  vi.restoreAllMocks();
});

describe("useRunJobs.refresh", () => {
  it("discards a stale response that resolves after a newer refresh() call", async () => {
    // Simulates the exact symptom reported in production: two overlapping
    // setInterval-driven polls resolve out of order (network jitter), and
    // the OLDER, smaller-progress response must not clobber the newer one.
    let resolveFirst!: (v: {jobs: RunJobSnapshot[]}) => void;
    const first = new Promise<{jobs: RunJobSnapshot[]}>((res) => { resolveFirst = res; });
    const second = Promise.resolve({
      jobs: [job({progress: {processed: 5803, total: 7822}})],
    });

    vi.spyOn(RunJobs, "listMine")
      .mockReturnValueOnce(first)
      .mockReturnValueOnce(second);

    const p1 = useRunJobs.getState().refresh();
    const p2 = useRunJobs.getState().refresh();
    await p2;
    expect(useRunJobs.getState().jobs.j1?.progress).toEqual({processed: 5803, total: 7822});

    // The slow first request now resolves with OLDER, smaller progress —
    // it must be discarded, not applied on top of the newer state.
    resolveFirst({jobs: [job({progress: {processed: 1023, total: 7822}})]});
    await p1;
    expect(useRunJobs.getState().jobs.j1?.progress).toEqual({processed: 5803, total: 7822});
  });

  it("applies responses normally when they resolve in order", async () => {
    vi.spyOn(RunJobs, "listMine").mockResolvedValueOnce({
      jobs: [job({progress: {processed: 100, total: 7822}})],
    });
    await useRunJobs.getState().refresh();
    expect(useRunJobs.getState().jobs.j1?.progress).toEqual({processed: 100, total: 7822});

    vi.spyOn(RunJobs, "listMine").mockResolvedValueOnce({
      jobs: [job({progress: {processed: 200, total: 7822}})],
    });
    await useRunJobs.getState().refresh();
    expect(useRunJobs.getState().jobs.j1?.progress).toEqual({processed: 200, total: 7822});
  });

  it("keeps terminal job snapshots when the active poll returns empty", async () => {
    useRunJobs.getState().upsertJob(job({
      id: "upload-1",
      kind: "wikidata_upload",
      status: "cancelled",
      params: {upload_target: "test"},
      progress: {processed: 201, total: 233, upload_target: "test"},
    }));
    vi.spyOn(RunJobs, "listMine").mockResolvedValueOnce({jobs: []});
    await useRunJobs.getState().refresh();
    expect(useRunJobs.getState().jobs["upload-1"]?.params?.upload_target).toBe("test");
    expect(useRunJobs.getState().jobs["upload-1"]?.status).toBe("cancelled");
  });
});
