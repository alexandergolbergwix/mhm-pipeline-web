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

  it("session.start activates judge and marks goal done", () => {
    const next = reduceFlow(makeInitialFlowState(), {
      type: "session.start",
    } as AgentEvent);
    expect(next.nodeStatus.goal).toBe("done");
    expect(next.nodeStatus.judge).toBe("active");
  });

  it("llm.turn increments stepCount and keeps judge active", () => {
    const start = reduceFlow(makeInitialFlowState(), {
      type: "session.start",
    } as AgentEvent);
    const next = reduceFlow(start, { type: "llm.turn" } as AgentEvent);
    expect(next.stepCount).toBe(1);
    expect(next.nodeStatus.judge).toBe("active");
  });

  it("tool.dispatch routes a known tool to the right target node", () => {
    const start = reduceFlow(makeInitialFlowState(), {
      type: "session.start",
    } as AgentEvent);
    const next = reduceFlow(start, {
      type: "tool.dispatch",
      tool: "inspect_state",
    } as AgentEvent);
    expect(next.nodeStatus.tools).toBe("active");
    expect(next.nodeStatus.state).toBe("active");
    expect(next.nodeStatus.policy).toBe("done");
  });

  it("tool.result with ok=true marks tools done, ok=false flags error", () => {
    const start = reduceFlow(makeInitialFlowState(), {
      type: "session.start",
    } as AgentEvent);
    const dispatch = reduceFlow(start, {
      type: "tool.dispatch",
      tool: "inspect_state",
    } as AgentEvent);

    const ok = reduceFlow(dispatch, {
      type: "tool.result",
      tool: "inspect_state",
      ok: true,
    } as AgentEvent);
    expect(ok.nodeStatus.tools).toBe("done");
    expect(ok.nodeStatus.state).toBe("done");
    expect(ok.nodeStatus.trace).toBe("active");

    const bad = reduceFlow(dispatch, {
      type: "tool.result",
      tool: "inspect_state",
      ok: false,
    } as AgentEvent);
    expect(bad.nodeStatus.tools).toBe("error");
    expect(bad.nodeStatus.state).toBe("error");
  });

  it("session.end marks final + judge done and sets finished=true", () => {
    const next = reduceFlow(makeInitialFlowState(), {
      type: "session.end",
    } as AgentEvent);
    expect(next.nodeStatus.final).toBe("done");
    expect(next.nodeStatus.judge).toBe("done");
    expect(next.finished).toBe(true);
  });

  it("llm.error flips judge to error without halting subsequent events", () => {
    const start = reduceFlow(makeInitialFlowState(), {
      type: "session.start",
    } as AgentEvent);
    const errored = reduceFlow(start, { type: "llm.error" } as AgentEvent);
    expect(errored.nodeStatus.judge).toBe("error");

    // Subsequent session.end should still mark final done.
    const ended = reduceFlow(errored, { type: "session.end" } as AgentEvent);
    expect(ended.finished).toBe(true);
  });
});


describe("reduceFlow — replay a synthetic trace", () => {
  it("replays a happy-path session and lands every node in a terminal state", () => {
    const trace: AgentEvent[] = [
      { type: "session.start" } as AgentEvent,
      { type: "llm.turn"      } as AgentEvent,
      { type: "tool.dispatch", tool: "inspect_state" } as AgentEvent,
      { type: "tool.result",   tool: "inspect_state", ok: true } as AgentEvent,
      { type: "llm.turn"      } as AgentEvent,
      { type: "tool.dispatch", tool: "summarize_feature_list" } as AgentEvent,
      { type: "tool.result",   tool: "summarize_feature_list", ok: true } as AgentEvent,
      { type: "session.final" } as AgentEvent,
      { type: "session.end"   } as AgentEvent,
    ];

    let state: FlowState = makeInitialFlowState();
    for (const ev of trace) state = reduceFlow(state, ev);

    expect(state.finished).toBe(true);
    expect(state.stepCount).toBe(2);
    expect(state.nodeStatus.judge).toBe("done");
    expect(state.nodeStatus.final).toBe("done");
    // Tools/state/features all touched at some point → done.
    expect(state.nodeStatus.tools).toBe("done");
    expect(state.nodeStatus.features).toBe("done");
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
        goal:     "done",
        judge:    "done",
        policy:   "done",
        tools:    "done",
        state:    "done",
        reports:  "idle",
        features: "idle",
        trace:    "done",
        final:    "done",
      },
      stepCount: 5,
      finished:  true,
    };
    expect(() =>
      render(<AgentFlowDiagram lastEvent={null} flow={final} />),
    ).not.toThrow();
  });
});
