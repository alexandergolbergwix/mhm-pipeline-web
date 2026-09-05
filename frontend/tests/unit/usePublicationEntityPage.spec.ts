import {act, renderHook, waitFor} from "@testing-library/react";
import {afterEach, describe, expect, it, vi} from "vitest";

import type {PublicationEntity, PublicationEntityPage} from "@/api/publication";
import {usePublicationEntityPage} from "@/hooks/usePublicationEntityPage";

function entity(id: string): PublicationEntity {
  return {
    entity_id: id,
    entity_digest: `sha256:${id}`,
    entity_kind: "work",
    label: id,
    description: null,
    target_qid: null,
    statement_count: 2,
    review_status: "pending",
    proposed_action: "create",
    findings: [],
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText: status === 404 ? "Not Found" : "OK",
    headers: {"Content-Type": "application/json"},
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("usePublicationEntityPage", () => {
  it("replaces the page when the curator moves through opaque cursors", async () => {
    const first: PublicationEntityPage = {
      release_id: "release-1",
      release_digest: "sha256:release-1",
      items: [entity("work-1")],
      next_cursor: "after-work-1",
      total: 2,
    };
    const second: PublicationEntityPage = {
      ...first,
      items: [entity("work-2")],
      next_cursor: null,
    };
    const fetchMock = vi.fn().mockImplementation(async (_url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body)) as {query?: {cursor?: string | null}};
      return jsonResponse(body.query?.cursor === "after-work-1" ? second : first);
    });
    vi.stubGlobal("fetch", fetchMock);

    const {result} = renderHook(() => usePublicationEntityPage({
      runId: "run-1",
      publicationId: "publication-1",
      releaseId: "release-1",
      releaseDigest: "sha256:release-1",
      limit: 1,
    }));

    await waitFor(() => expect(result.current.items[0]?.entity_id).toBe("work-1"));
    expect(result.current.hasNext).toBe(true);
    expect(result.current.hasPrevious).toBe(false);

    act(() => result.current.next());
    await waitFor(() => expect(result.current.items[0]?.entity_id).toBe("work-2"));
    expect(result.current.hasNext).toBe(false);
    expect(result.current.hasPrevious).toBe(true);

    act(() => result.current.previous());
    await waitFor(() => expect(result.current.items[0]?.entity_id).toBe("work-1"));
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("uses a bounded legacy page when the publication route does not exist", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({detail: "Not found"}, 404));
    vi.stubGlobal("fetch", fetchMock);
    const compatibilityItems = [entity("work-1"), entity("work-2"), entity("work-3")];

    const {result} = renderHook(() => usePublicationEntityPage({
      runId: "run-1",
      publicationId: "publication-compatibility",
      releaseId: "release-compatibility",
      releaseDigest: "legacy:1",
      limit: 2,
      compatibilityItems,
    }));

    await waitFor(() => expect(result.current.source).toBe("compatibility"));
    expect(result.current.items.map((item) => item.entity_id)).toEqual(["work-1", "work-2"]);
    expect(result.current.hasNext).toBe(true);

    act(() => result.current.next());
    await waitFor(() => expect(result.current.items.map((item) => item.entity_id)).toEqual(["work-3"]));
    expect(result.current.hasNext).toBe(false);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("uses current data without an HTTP request before a Publication exists", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const {result} = renderHook(() => usePublicationEntityPage({
      runId: "run-1",
      publicationId: null,
      releaseId: "release-compatibility",
      releaseDigest: "legacy:1",
      limit: 1,
      compatibilityItems: [entity("work-1"), entity("work-2")],
    }));

    await waitFor(() => expect(result.current.items[0]?.entity_id).toBe("work-1"));
    expect(result.current.source).toBe("compatibility");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
