import {render} from "@testing-library/react";
import {createElement} from "react";
import {afterEach, describe, expect, it, vi} from "vitest";

import {ExtractionApprovals, type Entity} from "@/api/extractionApprovals";
import {useApprovalStore} from "@/hooks/useApprovalStore";

vi.mock("@/stores/auth", () => ({useAuth: () => undefined}));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("explicit extraction rejection", () => {
  it("sends rejection separately from an approval reset", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response("{}", {
      status: 200, headers: {"Content-Type": "application/json"},
    }));
    vi.stubGlobal("fetch", fetchMock);

    await ExtractionApprovals.patch("run-1", "entity-1", {approved: false, rejected: true});
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      approved: false, rejected: true,
    });
    await ExtractionApprovals.bulkApprove("run-1", ["entity-1"], false, true);
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      entity_ids: ["entity-1"], approved: false, rejected: true,
    });
    await ExtractionApprovals.bulkApprove("run-1", ["entity-1"], false);
    expect(JSON.parse(String(fetchMock.mock.calls[2]?.[1]?.body))).toEqual({
      entity_ids: ["entity-1"], approved: false, rejected: false,
    });
  });

  it("accepts a rejected-only change on the next poll", async () => {
    let rejected = false;
    const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify({
      entities: [{id: "entity-1", control_number: "990001", approved: false, rejected}], total: 1,
    }), {status: 200, headers: {"Content-Type": "application/json"}}));
    vi.stubGlobal("fetch", fetchMock);
    let entities: Entity[] = [];
    function Harness() {
      entities = useApprovalStore("run-1", {idleIntervalMs: 20}).entities;
      return null;
    }
    const view = render(createElement(Harness));
    await vi.waitFor(() => expect(entities[0]?.rejected).toBe(false));
    rejected = true;
    await vi.waitFor(() => expect(entities[0]?.rejected).toBe(true));
    view.unmount();
  });
});
