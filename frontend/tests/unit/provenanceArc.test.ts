import {describe, it, expect} from "vitest";
import {
  quadraticArcPoints,
  timeBounds,
  stopRevealedAt,
  mappableStops,
} from "@/components/research/provenanceArc";
import type {MapStop} from "@/api/research";

function stop(partial: Partial<MapStop>): MapStop {
  return {
    kind: "owner",
    label: "x",
    lat: 0,
    lon: 0,
    certain: false,
    inferred_geo: false,
    time: null,
    has_point: true,
    ...partial,
  } as MapStop;
}

describe("quadraticArcPoints", () => {
  it("starts and ends at the endpoints", () => {
    const pts = quadraticArcPoints([10, 20], [30, 40], 10);
    expect(pts[0]).toEqual([10, 20]);
    expect(pts[pts.length - 1]).toEqual([30, 40]);
  });

  it("returns samples+1 points", () => {
    expect(quadraticArcPoints([0, 0], [1, 1], 16).length).toBe(17);
  });

  it("bows away from the straight chord (control offset present)", () => {
    const straightMid: [number, number] = [5, 5];
    const pts = quadraticArcPoints([0, 0], [10, 10], 24, 0.3);
    const mid = pts[Math.floor(pts.length / 2)];
    const dist = Math.hypot(mid[0] - straightMid[0], mid[1] - straightMid[1]);
    expect(dist).toBeGreaterThan(0.1);
  });
});

describe("timeBounds", () => {
  it("returns null when no dated stops", () => {
    expect(timeBounds([stop({time: null}), stop({time: null})])).toBeNull();
  });

  it("computes min/max over dated stops", () => {
    const b = timeBounds([stop({time: 1500}), stop({time: 1700}), stop({time: null})]);
    expect(b).toEqual({min: 1500, max: 1700});
  });
});

describe("stopRevealedAt", () => {
  it("reveals undated waypoints always", () => {
    expect(stopRevealedAt(stop({time: null}), 0, 1700)).toBe(true);
  });

  it("reveals dated stop only once year reached", () => {
    const s = stop({time: 1600});
    expect(stopRevealedAt(s, 1500, 1700)).toBe(false);
    expect(stopRevealedAt(s, 1600, 1700)).toBe(true);
    expect(stopRevealedAt(s, 1650, 1700)).toBe(true);
  });

  it("reveals present-day holder only at max year", () => {
    const s = stop({kind: "current_holder", is_present: true, time: null});
    expect(stopRevealedAt(s, 1600, 1700)).toBe(false);
    expect(stopRevealedAt(s, 1700, 1700)).toBe(true);
  });
});

describe("mappableStops", () => {
  it("keeps only stops with a usable coordinate", () => {
    const s = [
      stop({has_point: true, lat: 1, lon: 2}),
      stop({has_point: false, lat: null, lon: null}),
      stop({has_point: true, lat: null, lon: null}),
    ];
    expect(mappableStops(s).length).toBe(1);
  });
});
