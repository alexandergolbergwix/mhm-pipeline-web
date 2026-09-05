import {render, screen, waitFor} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {afterEach, describe, expect, it, vi} from "vitest";

import {WikidataPublicationAudit} from "@/components/wikidata/WikidataPublicationAudit";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: {"Content-Type": "application/json"},
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("WikidataPublicationAudit", () => {
  it("reads bounded audit pages and moves through opaque cursors", async () => {
    const fetchMock = vi.fn().mockImplementation(async (_url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body)) as {query?: {cursor?: string | null; limit?: number}};
      if (body.query?.cursor === "audit-next") {
        return jsonResponse({
          publication_id: "publication-1",
          items: [{
            event_id: "event-2",
            sequence: 2,
            event_type: "execution.completed",
            occurred_at: "2026-09-05T10:02:00Z",
            actor_label: "Test Curator",
            release_id: "release-1",
            entity_id: null,
            message: "Execution completed.",
            details: {succeeded: 100000},
          }],
          next_cursor: null,
          total: 2,
        });
      }
      return jsonResponse({
        publication_id: "publication-1",
        items: [{
          event_id: "event-1",
          sequence: 1,
          event_type: "release.prepared",
          occurred_at: "2026-09-05T10:00:00Z",
          actor_label: "Test Curator",
          release_id: "release-1",
          entity_id: null,
          message: "Release prepared.",
          details: {},
        }],
        next_cursor: "audit-next",
        total: 2,
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<WikidataPublicationAudit runId="run-1" publicationId="publication-1" pageSize={1} />);

    await screen.findByText("Release prepared.");
    expect(screen.getByTestId("publication-audit-total")).toHaveTextContent("2 audit events");
    await userEvent.click(screen.getByTestId("publication-audit-next"));
    await screen.findByText("Execution completed.");

    const lastCall = fetchMock.mock.calls[1] ?? [];
    const lastBody = JSON.parse(String(lastCall[1]?.body));
    expect(lastBody).toEqual({query: {type: "audit", cursor: "audit-next", limit: 1}});
    expect(screen.getByTestId("publication-audit-previous")).toBeEnabled();
  });
});
