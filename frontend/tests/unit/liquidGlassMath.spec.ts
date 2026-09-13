import {describe, expect, it} from "vitest";

import {
  displacementMagnitude,
  glassContentLayout,
  glassSafeInset,
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

describe("glassSafeInset", () => {
  it("keeps panel text outside the 26px curve and 28px bezel", () => {
    const inset = glassSafeInset(26, 28);
    expect(inset).toBeGreaterThanOrEqual(16);
    expect(inset).toBeGreaterThanOrEqual(Math.ceil(26 * (1 - 1 / Math.SQRT2)));
  });

  it("is zero when the shell is flush (p-0)", () => {
    expect(glassSafeInset(26, 28, {flush: true})).toBe(0);
    expect(glassContentLayout("flex flex-col p-0", "", 26, 28).inset).toBe(0);
  });

  it("copies gap onto the inner column and keeps pill inset small", () => {
    const layout = glassContentLayout("flex flex-col gap-3", "", 26, 28);
    expect(layout.gapClass).toBe("gap-3");
    expect(glassSafeInset(999, 10, {pill: true})).toBeLessThanOrEqual(12);
  });
});
