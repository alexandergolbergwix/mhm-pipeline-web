import {afterEach, describe, expect, it, vi} from "vitest";

import {PublicationApi} from "@/api/publication";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: {"Content-Type": "application/json"},
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PublicationApi", () => {
  it("prepares a run-scoped Publication with the stable request shape", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      publication: {publication_id: "publication-1"},
    }));
    vi.stubGlobal("fetch", fetchMock);

    await PublicationApi.prepare("run-1", {
      profile_id: "mhm-wikidata",
      profile_version: "1",
      target: "live",
      source: {
        kind: "run",
        projection_source: "canonical",
        approved_only: true,
      },
    });

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("/api/runs/run-1/wikidata-publications/prepare");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      profile_id: "mhm-wikidata",
      profile_version: "1",
      target: "live",
      source: {
        kind: "run",
        projection_source: "canonical",
        approved_only: true,
      },
    });
  });

  it("advances a Publication with a digest-bound review command", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      publication: {publication_id: "publication-1"},
    }));
    vi.stubGlobal("fetch", fetchMock);

    await PublicationApi.advance("run-1", "publication-1", {
      type: "review",
      release_id: "release-2",
      expected_release_digest: "sha256:release",
      selection: {mode: "entities", entity_keys: ["work-1"]},
      decision: "approve",
      reason: "Checked against the source.",
    });

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("/api/runs/run-1/wikidata-publications/publication-1/advance");
    expect(JSON.parse(String(init?.body))).toEqual({
      command: {
        type: "review",
        release_id: "release-2",
        expected_release_digest: "sha256:release",
        selection: {mode: "entities", entity_keys: ["work-1"]},
        decision: "approve",
        reason: "Checked against the source.",
      },
    });
  });

  it("reads a cursor page through the run-scoped query endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      items: [],
      next_cursor: null,
    }));
    vi.stubGlobal("fetch", fetchMock);

    await PublicationApi.read("run-1", "publication-1", {
      type: "entities",
      release_id: "release-2",
      cursor: "cursor-50",
      limit: 50,
    });

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("/api/runs/run-1/wikidata-publications/publication-1/read");
    expect(JSON.parse(String(init?.body))).toEqual({
      query: {
        type: "entities",
        release_id: "release-2",
        cursor: "cursor-50",
        limit: 50,
      },
    });
  });
});
