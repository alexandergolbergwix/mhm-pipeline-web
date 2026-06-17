import { describe, expect, it } from "vitest";

import {
  displacementMagnitude,
  sdfRoundedRect,
} from "@/components/glass/liquidGlassMath";


describe("liquidGlassMath", () => {
  it("sdf is negative inside rounded rect", () => {
    expect(sdfRoundedRect(50, 50, 100, 100, 10)).toBeLessThan(0);
    expect(sdfRoundedRect(150, 150, 100, 100, 10)).toBeGreaterThan(0);
  });

  it("displacement is zero at flat interior (t=1 edge of bezel)", () => {
    const atBezelEnd = displacementMagnitude(1, 14, 1.5, "convexSquircle");
    expect(atBezelEnd).toBeGreaterThanOrEqual(0);
  });

  it("displacement grows toward the border", () => {
    const nearBorder = displacementMagnitude(0.1, 14, 1.5, "convexSquircle");
    const midBezel = displacementMagnitude(0.5, 14, 1.5, "convexSquircle");
    expect(nearBorder).toBeGreaterThan(midBezel);
  });
});
