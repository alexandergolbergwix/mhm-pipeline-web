/**
 * AgentFlowDiagram — reducer + render smoke.
 *
 * The diagram's visual state is driven entirely by `reduceFlow`, a pure
 * function that takes the previous flow state + a single AgentEvent
 * and returns the next state. Testing the reducer in isolation lets us
 * replay a synthetic trace and snapshot the resulting state without
 * rendering SVG (or wrestling with jsdom's lack of SVG layout).
 *
 * We also render the component once with a final-state flow to ensure
 * the SVG mounts cleanly under the jsdom environment.
 */

import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import {
  AgentFlowDiagram,
  makeInitialFlowState,
  reduceFlow,
  type FlowState,
} from "@/components/AgentFlowDiagram";
import type { AgentEvent } from "@/api/aiVerify";


describe("reduceFlow — pure reducer", () => {
  it("initial state has every node idle and zero steps", () => {
    const s = makeInitialFlowState();
    expect(s.stepCount).toBe(0);
    expect(s.finished).toBe(false);
    for (const status of Object.values(s.nodeStatus)) {
      expect(status).toBe("idle");
    }
  });

  it("session.start activates candidate intake and records scope size", () => {
    const next = reduceFlow(makeInitialFlowState(), {
      type: "session.start",
      scope_size: 3,
      cache_hits: 2,
    } as AgentEvent);
    expect(next.nodeStatus.inputs).toBe("done");
    expect(next.nodeStatus.rubric).toBe("done");
    expect(next.nodeStatus.candidates).toBe("active");
    expect(next.total).toBe(3);
    expect(next.cacheHits).toBe(2);
    expect(next.nodeStatus.cache).toBe("active");
  });

  it("agent.stats routes cache misses to the judge", () => {
    const start = reduceFlow(makeInitialFlowState(), {
      type: "session.start",
    } as AgentEvent);
    const next = reduceFlow(start, {
      type: "agent.stats",
      judged: 1,
      total: 2,
    } as AgentEvent);
    expect(next.judged).toBe(1);
    expect(next.total).toBe(2);
    expect(next.nodeStatus.cache).toBe("done");
    expect(next.nodeStatus.judge).toBe("active");
    expect(next.lastEdge).toEqual(["cache", "judge"]);
  });

  it("agent.stats routes cache hits straight to verdict", () => {
    const start = reduceFlow(makeInitialFlowState(), {
      type: "session.start",
    } as AgentEvent);
    const next = reduceFlow(start, {
      type: "agent.stats",
      hits: 1,
    } as AgentEvent);
    expect(next.cacheHits).toBe(1);
    expect(next.nodeStatus.cache).toBe("active");
    expect(next.lastEdge).toEqual(["cache", "verdict"]);
  });

  it("runner.step marks tool and escalation activity", () => {
    const start = reduceFlow(makeInitialFlowState(), {
      type: "session.start",
    } as AgentEvent);
    const toolStep = reduceFlow(start, {
      type: "runner.step",
      message: "tool inspect_state",
    } as AgentEvent);
    expect(toolStep.nodeStatus.judge).toBe("done");
    expect(toolStep.nodeStatus.tools).toBe("active");
    expect(toolStep.stepCount).toBe(1);
    expect(toolStep.lastEdge).toEqual(["judge", "tools"]);

    const escalateStep = reduceFlow(start, {
      type: "runner.step",
      message: "escalate abstain",
    } as AgentEvent);
    expect(escalateStep.nodeStatus.escalate).toBe("active");
    expect(escalateStep.stepCount).toBe(1);
    expect(escalateStep.lastEdge).toEqual(["judge", "escalate"]);
  });

  it("agent.verdict marks verdict active and completes the source node", () => {
    const start = reduceFlow(makeInitialFlowState(), {
      type: "runner.step",
      message: "tool inspect_state",
    } as AgentEvent);
    const next = reduceFlow(start, { type: "agent.verdict" } as AgentEvent);
    expect(next.nodeStatus.verdict).toBe("active");
    expect(next.nodeStatus.judge).toBe("done");
    expect(next.lastEdge).toEqual(["judge", "verdict"]);
  });

  it("session.end marks verdict/report done and sets finished=true", () => {
    const next = reduceFlow(makeInitialFlowState(), {
      type: "session.end",
    } as AgentEvent);
    expect(next.nodeStatus.verdict).toBe("done");
    expect(next.nodeStatus.report).toBe("done");
    expect(next.finished).toBe(true);
  });

  it("llm.error flips judge to error without halting subsequent events", () => {
    const start = reduceFlow(makeInitialFlowState(), {
      type: "session.start",
    } as AgentEvent);
    const errored = reduceFlow(start, { type: "llm.error" } as AgentEvent);
    expect(errored.nodeStatus.judge).toBe("error");

    // Subsequent session.end should still mark the report done.
    const ended = reduceFlow(errored, { type: "session.end" } as AgentEvent);
    expect(ended.finished).toBe(true);
  });
});


describe("reduceFlow — replay a synthetic trace", () => {
  it("replays a happy-path session and lands every node in a terminal state", () => {
    const trace: AgentEvent[] = [
      { type: "session.start" } as AgentEvent,
      { type: "agent.stats", judged: 1, total: 2 } as AgentEvent,
      { type: "runner.step", message: "tool inspect_state" } as AgentEvent,
      { type: "agent.verdict" } as AgentEvent,
      { type: "agent.stats", hits: 1, judged: 1, total: 2 } as AgentEvent,
      { type: "session.end"   } as AgentEvent,
    ];

    let state: FlowState = makeInitialFlowState();
    for (const ev of trace) state = reduceFlow(state, ev);

    expect(state.finished).toBe(true);
    expect(state.stepCount).toBe(1);
    expect(state.nodeStatus.judge).toBe("done");
    expect(state.nodeStatus.verdict).toBe("done");
    expect(state.nodeStatus.report).toBe("done");
    expect(state.cacheHits).toBe(1);
    expect(state.judged).toBe(1);
  });
});


describe("<AgentFlowDiagram> render smoke", () => {
  it("renders an SVG with every node label visible", () => {
    const { container } = render(
      <AgentFlowDiagram lastEvent={null} flow={makeInitialFlowState()} />,
    );
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    // 9 nodes × 2 text labels each (label + hint) ≥ 18 <text> elements.
    expect(container.querySelectorAll("text").length).toBeGreaterThanOrEqual(18);
  });

  it("re-renders with an updated flow without throwing", () => {
    const final: FlowState = {
      nodeStatus: {
        inputs:     "done",
        rubric:     "done",
        candidates: "done",
        cache:      "done",
        judge:      "done",
        tools:      "done",
        escalate:   "idle",
        verdict:    "done",
        report:     "done",
      },
      stepCount: 5,
      finished:  true,
      cacheHits: 1,
      judged:    4,
      total:     5,
      lastEdge:  null,
    };
    expect(() =>
      render(<AgentFlowDiagram lastEvent={null} flow={final} />),
    ).not.toThrow();
  });
});
