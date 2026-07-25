import {describe, expect, it, vi} from "vitest";

import {
  createThrottledProgressRefresh,
  jobProcessedCount,
} from "@/utils/throttledProgressRefresh";

describe("createThrottledProgressRefresh", () => {
  it("fires on the first positive processed count", () => {
    const t = createThrottledProgressRefresh({minMs: 10_000, minDelta: 100});
    expect(t.shouldRefresh(0)).toBe(false);
    expect(t.shouldRefresh(1)).toBe(true);
    expect(t.shouldRefresh(2)).toBe(false);
  });

  it("fires again when processed jumps by minDelta", () => {
    const t = createThrottledProgressRefresh({minMs: 10_000, minDelta: 5});
    expect(t.shouldRefresh(1)).toBe(true);
    expect(t.shouldRefresh(4)).toBe(false);
    expect(t.shouldRefresh(6)).toBe(true);
  });

  it("fires again after minMs even for small deltas", () => {
    vi.useFakeTimers();
    const t = createThrottledProgressRefresh({minMs: 3000, minDelta: 100});
    expect(t.shouldRefresh(1)).toBe(true);
    expect(t.shouldRefresh(2)).toBe(false);
    vi.advanceTimersByTime(3000);
    expect(t.shouldRefresh(2)).toBe(true);
    vi.useRealTimers();
  });

  it("reset allows the next tick to fire immediately", () => {
    const t = createThrottledProgressRefresh({minMs: 10_000, minDelta: 100});
    expect(t.shouldRefresh(1)).toBe(true);
    expect(t.shouldRefresh(2)).toBe(false);
    t.reset();
    expect(t.shouldRefresh(1)).toBe(true);
  });
});

describe("jobProcessedCount", () => {
  it("reads progress.processed safely", () => {
    expect(jobProcessedCount(null)).toBe(0);
    expect(jobProcessedCount({progress: null})).toBe(0);
    expect(jobProcessedCount({progress: {processed: 12}})).toBe(12);
  });
});
