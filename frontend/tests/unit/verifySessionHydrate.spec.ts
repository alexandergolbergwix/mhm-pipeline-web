import {describe, expect, it} from "vitest";

import {
  applyJobProgressToFlow,
  hydrateVerifySession,
  traceLineToAgentEvent,
} from "@/utils/verifySessionHydrate";
import {makeInitialFlowState} from "@/components/AgentFlowDiagram";

describe("hydrateVerifySession", () => {
  it("replays trace events for flow colours", () => {
    const hydrated = hydrateVerifySession(
      {
        events: [
          {type: "session.start", scope_size: 2, ts: "2026-01-01T00:00:00Z"},
          {type: "agent.stats", judged: 1, total: 2, ts: "2026-01-01T00:00:01Z"},
        ],
        verdicts: [],
      },
      () => "",
    );
    expect(hydrated.flow.nodeStatus.inputs).toBe("done");
    expect(hydrated.flow.nodeStatus.judge).toBe("active");
    expect(hydrated.flow.judged).toBe(1);
  });

  it("maps verdict rows by local id", () => {
    const hydrated = hydrateVerifySession(
      {
        events: [],
        verdicts: [{
          candidate: {_local_id: "person::Foo"},
          verdict: {overall: "pass"},
        }],
      },
      (row) => String((row.candidate as {_local_id?: string})?._local_id ?? ""),
    );
    expect(Object.keys(hydrated.verdicts)).toEqual(["person::Foo"]);
    expect(hydrated.flow.nodeStatus.inputs).toBe("done");
    expect(hydrated.flow.nodeStatus.verdict).toBe("active");
  });
});

describe("applyJobProgressToFlow", () => {
  it("lights judge while background job is running", () => {
    const next = applyJobProgressToFlow(makeInitialFlowState(), {
      phase: "running",
      processed: 3,
      total: 10,
      last_event_type: "agent.verdict",
    });
    expect(next.nodeStatus.inputs).toBe("done");
    expect(next.nodeStatus.judge).toBe("active");
    expect(next.judged).toBe(3);
    expect(next.total).toBe(10);
  });
});

describe("traceLineToAgentEvent", () => {
  it("strips ts from trace lines", () => {
    const ev = traceLineToAgentEvent({
      ts: "2026-01-01",
      type: "session.start",
      scope_size: 5,
    });
    expect(ev.type).toBe("session.start");
    expect(ev.scope_size).toBe(5);
    expect(ev.ts).toBeUndefined();
  });
});
