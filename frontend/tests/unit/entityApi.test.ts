/**
 * Unit tests for the entity detail API wrapper (Feature 3 — Phase B).
 *
 * Tests the TypeScript shape and URL construction of entityApi.getEntityDetail.
 * Network calls are mocked with vi.fn() so no server needed.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock the api client before importing entityApi
vi.mock("@/api/client", () => ({
  api: {
    get: vi.fn(),
  },
}));

import { api } from "@/api/client";
import { entityApi, type EntityDetailResponse } from "@/api/entities";

const mockGet = vi.mocked(api.get);

beforeEach(() => {
  mockGet.mockReset();
});

describe("entityApi.getEntityDetail", () => {
  it("calls the correct URL", async () => {
    const expected: EntityDetailResponse = {
      uri: "urn:person:maimonides",
      label: "Moses Maimonides",
      type: "person",
      roles: ["author"],
      manuscripts: [],
      dates: null,
      geo: null,
      identifiers: {},
    };
    mockGet.mockResolvedValueOnce(expected);

    await entityApi.getEntityDetail("proj-1", "urn:person:maimonides");

    expect(mockGet).toHaveBeenCalledOnce();
    const [url] = mockGet.mock.calls[0] as [string];
    expect(url).toContain("/projects/proj-1/research/entity");
    expect(url).toContain(encodeURIComponent("urn:person:maimonides"));
  });

  it("returns the response as-is", async () => {
    const expected: EntityDetailResponse = {
      uri: "urn:place:cairo",
      label: "Cairo",
      type: "place",
      roles: [],
      manuscripts: [{ uri: "urn:hm:MS_001", label: "MS A", role: "production" }],
      dates: null,
      geo: { lat: 30.04, lon: 31.23 },
      identifiers: {},
    };
    mockGet.mockResolvedValueOnce(expected);

    const result = await entityApi.getEntityDetail("proj-2", "urn:place:cairo");
    expect(result).toEqual(expected);
  });

  it("returns identifiers with viaf and wikidata", async () => {
    const expected: EntityDetailResponse = {
      uri: "urn:person:rashi",
      label: "Rashi",
      type: "person",
      roles: ["author"],
      manuscripts: [],
      dates: { birth: 1040, death: 1105 },
      geo: null,
      identifiers: { viaf: "12345", wikidata: "Q189564" },
    };
    mockGet.mockResolvedValueOnce(expected);

    const result = await entityApi.getEntityDetail("proj-3", "urn:person:rashi");
    expect(result.identifiers.viaf).toBe("12345");
    expect(result.identifiers.wikidata).toBe("Q189564");
    expect(result.dates?.birth).toBe(1040);
  });
});

describe("EntityDetailResponse type shape", () => {
  it("type is one of the expected values", () => {
    const validTypes = ["person", "place", "work", "manuscript", "entity"];
    const r: EntityDetailResponse = {
      uri: "x",
      label: null,
      type: "person",
      roles: [],
      manuscripts: [],
      dates: null,
      geo: null,
      identifiers: {},
    };
    expect(validTypes).toContain(r.type);
  });

  it("manuscripts array has correct shape", () => {
    const ms = { uri: "urn:hm:MS_001", label: "Test", role: "author" };
    const r: EntityDetailResponse = {
      uri: "x",
      label: null,
      type: "person",
      roles: ["author"],
      manuscripts: [ms],
      dates: null,
      geo: null,
      identifiers: {},
    };
    expect(r.manuscripts[0].uri).toBe("urn:hm:MS_001");
    expect(r.manuscripts[0].role).toBe("author");
  });
});
