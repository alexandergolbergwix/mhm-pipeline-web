import {afterEach, describe, expect, it, vi} from "vitest";

import {
  clearGlassMapMemoryCache,
  glassCacheKey,
  getGlassMaps,
  normalizeGlassMapOptions,
} from "@/components/glass/glassMapCache";
import type {GlassMaps} from "@/components/glass/liquidGlassMath";

const fakeMaps = (tag: string): GlassMaps => ({
  displacementUrl: `data:image/png;base64,${tag}-disp`,
  specularUrl: `data:image/png;base64,${tag}-spec`,
  scale: 12,
});

vi.mock("@/components/glass/liquidGlassMath", () => ({
  buildGlassMaps: vi.fn(() => fakeMaps("built")),
  supportsSvgBackdropFilter: vi.fn(() => false),
  displacementMagnitude: vi.fn(),
  sdfRoundedRect: vi.fn(),
}));

import {buildGlassMaps} from "@/components/glass/liquidGlassMath";

const mockedBuild = vi.mocked(buildGlassMaps);

describe("glassMapCache", () => {
  afterEach(() => {
    clearGlassMapMemoryCache();
    mockedBuild.mockClear();
    mockedBuild.mockImplementation(() => fakeMaps("built"));
    localStorage.clear();
  });

  it("quantizes dimensions to an 8px grid", () => {
    expect(normalizeGlassMapOptions({
      width: 103,
      height: 57,
      radius: 20,
      bezelWidth: 28,
      thickness: 14,
    })).toEqual({
      width: 104,
      height: 56,
      radius: 20,
      bezelWidth: 28,
      thickness: 14,
      ior: 1.5,
      surface: "convexSquircle",
    });
  });

  it("maps nearby sizes to the same cache key", () => {
    const a = glassCacheKey({
      width: 400,
      height: 200,
      radius: 20,
      bezelWidth: 28,
      thickness: 14,
    });
    const b = glassCacheKey({
      width: 403,
      height: 197,
      radius: 20,
      bezelWidth: 28,
      thickness: 14,
    });
    expect(a).toBe(b);
  });

  it("returns memory cache on second call without rebuilding", () => {
    const opts = {
      width: 120,
      height: 48,
      radius: 999,
      bezelWidth: 12,
      thickness: 8,
    };
    const first = getGlassMaps(opts);
    const second = getGlassMaps({...opts, width: 121});

    expect(second).toBe(first);
    expect(mockedBuild).toHaveBeenCalledTimes(1);
  });

  it("hydrates from localStorage after memory clear", async () => {
    const opts = {
      width: 96,
      height: 40,
      radius: 16,
      bezelWidth: 20,
      thickness: 10,
    };
    const first = getGlassMaps(opts);

    await new Promise<void>((resolve) => {
      if (typeof requestIdleCallback !== "undefined") {
        requestIdleCallback(() => resolve(), {timeout: 100});
      } else {
        setTimeout(resolve, 0);
      }
    });

    clearGlassMapMemoryCache();
    const second = getGlassMaps(opts);

    expect(second.displacementUrl).toBe(first.displacementUrl);
    expect(second.specularUrl).toBe(first.specularUrl);
    expect(second.scale).toBe(first.scale);
    expect(mockedBuild).toHaveBeenCalledTimes(1);
  });
});
