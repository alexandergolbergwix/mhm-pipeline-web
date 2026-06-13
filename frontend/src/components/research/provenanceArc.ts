/**
 * Pure helpers for the provenance movement map — kept free of React/Leaflet so
 * they can be unit-tested in vitest.
 */
import type {MapStop} from "@/api/research";

export type LatLng = [number, number];

/**
 * Sample points along a quadratic Bézier curve between two lat/lon points.
 * The control point is offset perpendicular to the chord midpoint by
 * `curvature × chordLength`, giving a consistent gentle arc (great-circle-ish
 * look) that disambiguates overlapping straight lines.
 */
export function quadraticArcPoints(
  from: LatLng,
  to: LatLng,
  samples = 24,
  curvature = 0.2,
): LatLng[] {
  const [y0, x0] = from;
  const [y1, x1] = to;
  const mx = (x0 + x1) / 2;
  const my = (y0 + y1) / 2;
  const dx = x1 - x0;
  const dy = y1 - y0;
  // Perpendicular offset (-dy, dx) normalised × curvature × chord length.
  const len = Math.hypot(dx, dy) || 1;
  const ox = (-dy / len) * curvature * len;
  const oy = (dx / len) * curvature * len;
  const cx = mx + ox;
  const cy = my + oy;

  const pts: LatLng[] = [];
  for (let i = 0; i <= samples; i++) {
    const t = i / samples;
    const it = 1 - t;
    const x = it * it * x0 + 2 * it * t * cx + t * t * x1;
    const y = it * it * y0 + 2 * it * t * cy + t * t * y1;
    pts.push([y, x]);
  }
  return pts;
}

/** Min/max of the dated stops' `time` (used to bound the year slider). */
export function timeBounds(stops: MapStop[]): {min: number; max: number} | null {
  const times = stops
    .map((s) => s.time)
    .filter((t): t is number => t !== null && t !== undefined);
  if (times.length === 0) return null;
  return {min: Math.min(...times), max: Math.max(...times)};
}

/**
 * Whether a stop is revealed at slider year `t`.
 * - present-day anchor (current_holder): only at/after the max year
 * - undated waypoint (time === null): always shown
 * - dated stop: shown once its time ≤ t
 */
export function stopRevealedAt(stop: MapStop, t: number, maxTime: number): boolean {
  if (stop.is_present) return t >= maxTime;
  if (stop.time === null || stop.time === undefined) return true;
  return stop.time <= t;
}

/** Stops with a usable coordinate, in graph order. */
export function mappableStops(stops: MapStop[]): MapStop[] {
  return stops.filter((s) => s.has_point && s.lat !== null && s.lon !== null);
}
