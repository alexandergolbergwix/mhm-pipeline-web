import {act, render, screen} from "@testing-library/react";
import {afterEach, describe, expect, it, vi} from "vitest";

import type {ReferenceRepairStatus} from "@/api/referenceRepairs";
import {ReferenceRepairProgress, repairProgress} from "@/components/wikidata/ReferenceRepairProgress";

const base: ReferenceRepairStatus = {
  enabled: true, scope_digest: "scope", run_id: "run", total: 100, verified: 7,
  already_clean: 3, completed: 10, halted_status: null, job: null, maxlag: 20,
};

afterEach(() => vi.useRealTimers());

describe("confirmed reference progress", () => {
  it("counts clean items separately and never infers completion from job status", () => {
    expect(repairProgress(base)).toMatchObject({completed: 10, remaining: 90, percent: 10});
    expect(repairProgress({...base, already_clean: undefined, completed: 90,
      job: {id: "job", status: "succeeded", progress: {}}}).completed).toBe(7);
  });

  it("explains the queue without a fake active repair or ETA", () => {
    render(<ReferenceRepairProgress state={{...base, job: {id: "job", status: "queued", progress: {phase: "queued"}}}}
      lastSync={Date.now()} connectionError={false} />);
    expect(screen.getByRole("heading", {name: "In queue"})).toBeTruthy();
    expect(screen.getByText(/worker is available/)).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("value")).toBe("10");
    expect(screen.queryByText(/Next retry/)).toBeNull();
  });

  it("updates the retry countdown without advancing confirmed progress", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-17T16:00:00Z"));
    render(<ReferenceRepairProgress state={{...base, job: {
      id: "job", status: "running", progress: {phase: "service_delay", current_qid: "Q123",
        next_attempt_at: "2026-11-17T16:00:10Z",
        lag_probe_status: "delayed", lag_probe_seconds: 20.4666,
        next_lag_probe_at: "2026-11-17T16:01:00Z"},
    }}} lastSync={Date.now()} connectionError={false} />);
    expect(screen.getByRole("heading", {name: "Wikidata delay"})).toBeTruthy();
    expect(screen.getByText("Next retry in 10s")).toBeTruthy();
    expect(screen.getByText(/Earliest edit retry: 18:00:10 Israel time/)).toBeTruthy();
    act(() => vi.advanceTimersByTime(4000));
    expect(screen.getByText("Next retry in 6s")).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("value")).toBe("10");
    act(() => vi.advanceTimersByTime(7000));
    expect(screen.getByText(/needs a successful lag check/)).toBeTruthy();
    expect(screen.getByText(/Reported lag: 20.47. Edit limit: 20/)).toBeTruthy();
    expect(screen.getByText(/Next check in 49s/)).toBeTruthy();
  });

  it("shows the completion estimate for a live job and hides a stale estimate", () => {
    const state: ReferenceRepairStatus = {...base, eta_seconds: 1500, job: {
      id: "job", status: "running", progress: {phase: "active"},
    }};
    const lastSync = Date.now();
    const {rerender} = render(<ReferenceRepairProgress state={state} lastSync={lastSync}
      connectionError={false} />);
    expect(screen.getByText("Estimated time left is 25m 0s.")).toBeTruthy();

    rerender(<ReferenceRepairProgress state={state} lastSync={lastSync} connectionError />);
    expect(screen.queryByText(/Estimated time left/)).toBeNull();
  });

  it("retains the last confirmed bar when the connection fails", () => {
    render(<ReferenceRepairProgress state={base} lastSync={Date.now()} connectionError />);
    expect(screen.getByText("Connection issue")).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("value")).toBe("10");
  });
});
