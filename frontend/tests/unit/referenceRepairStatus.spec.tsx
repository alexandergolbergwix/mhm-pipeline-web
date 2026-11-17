import {act, renderHook, waitFor} from "@testing-library/react";
import {beforeEach, describe, expect, it, vi} from "vitest";

import {ApiError} from "@/api/client";
import {useProjectEvents} from "@/api/realtime";
import type {RunJobSnapshot} from "@/api/runJobs";
import {ReferenceRepairs, type ReferenceRepairStatus} from "@/api/referenceRepairs";
import {useReferenceRepairStatus} from "@/hooks/useReferenceRepairStatus";

vi.mock("@/api/referenceRepairs", () => ({ReferenceRepairs: {status: vi.fn()}}));
vi.mock("@/api/realtime", () => ({useProjectEvents: vi.fn()}));
vi.mock("@/stores/runJobs", () => ({useRunJobs: {getState: () => ({upsertJob: vi.fn()})}}));

const status: ReferenceRepairStatus = {
  enabled: true, scope_digest: "scope", run_id: "run", total: 100,
  verified: 5, halted_status: null, job: null,
};

beforeEach(() => vi.clearAllMocks());

describe("live reference status", () => {
  it("refreshes from a matching job push and ignores another run", async () => {
    vi.mocked(ReferenceRepairs.status).mockResolvedValue({...status, project_id: "project"});
    renderHook(() => useReferenceRepairStatus());
    await waitFor(() => expect(ReferenceRepairs.status).toHaveBeenCalledTimes(1));
    const handler = vi.mocked(useProjectEvents).mock.calls.at(-1)?.[1];
    if (!handler) throw new Error("The project event handler is absent.");
    const job: RunJobSnapshot = {id: "job", project_id: "project", run_id: "other",
      kind: "wikidata_reference_repair", status: "running", progress: {}, params: {},
      result: null, error: null, created_by: null, started_at: null, finished_at: null,
      cancel_requested_at: null, created_at: null, updated_at: null};
    act(() => handler({project_id: "project", type: "run_job_update", job}));
    expect(ReferenceRepairs.status).toHaveBeenCalledTimes(1);
    act(() => handler({project_id: "project", type: "run_job_update", job: {...job, run_id: "run"}}));
    await waitFor(() => expect(ReferenceRepairs.status).toHaveBeenCalledTimes(2));
  });

  it("preserves progress on a failed refresh and clears the error after recovery", async () => {
    vi.mocked(ReferenceRepairs.status).mockResolvedValueOnce(status)
      .mockRejectedValueOnce(new Error("offline")).mockResolvedValue(status);
    const {result} = renderHook(() => useReferenceRepairStatus());
    await waitFor(() => expect(result.current.state?.verified).toBe(5));
    act(() => result.current.refresh());
    await waitFor(() => expect(result.current.error).toBe("offline"));
    expect(result.current.state?.verified).toBe(5);
    act(() => result.current.refresh());
    await waitFor(() => expect(result.current.error).toBe(""));
  });

  it("clears protected data when administrator access is revoked", async () => {
    vi.mocked(ReferenceRepairs.status).mockResolvedValueOnce(status)
      .mockRejectedValue(new ApiError(403, "Admin role required"));
    const {result} = renderHook(() => useReferenceRepairStatus());
    await waitFor(() => expect(result.current.state).not.toBeNull());
    act(() => result.current.refresh());
    await waitFor(() => expect(result.current.state).toBeNull());
    expect(result.current.error).toBe("Admin role required");
  });

  it("does not overlap reads or let an old response replace a newly accepted job", async () => {
    let resolve: (value: ReferenceRepairStatus) => void = () => {};
    const pending = new Promise<ReferenceRepairStatus>(done => { resolve = done; });
    vi.mocked(ReferenceRepairs.status).mockResolvedValueOnce(status).mockReturnValueOnce(pending)
      .mockResolvedValue({...status, job: {id: "new", status: "queued", progress: {}}});
    const {result} = renderHook(() => useReferenceRepairStatus());
    await waitFor(() => expect(result.current.state).not.toBeNull());
    act(() => { result.current.refresh(); result.current.refresh(); });
    expect(ReferenceRepairs.status).toHaveBeenCalledTimes(2);
    act(() => result.current.acceptJob({id: "new", status: "queued", progress: {}}));
    await act(async () => resolve(status));
    await waitFor(() => expect(result.current.state?.job?.id).toBe("new"));
  });
});
