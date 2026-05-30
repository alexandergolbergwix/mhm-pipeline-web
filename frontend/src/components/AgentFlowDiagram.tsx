/**
 * AgentFlowDiagram — live SVG visualisation of the orchestrator's flow.
 *
 * Mirrors the desktop's PyQt AgentSystemDiagram in spirit: nodes are
 * the surfaces the orchestrator can touch (state readers, tools,
 * judge, policy, trace), edges are data flow, and tool calls animate
 * a particle along the matching edge for ~700ms.
 *
 * The component is purely presentational — it doesn't fetch or stream.
 * Feed it a sequence of OrchestratorEvent dicts via the `lastEvent`
 * prop; it animates the right node and shows the right pill.
 *
 * Three node states per node:
 *  - idle      (default — translucent, thin border)
 *  - active    (pulsing glow, brighter fill)
 *  - done      (success border, dimmed)
 *  - error     (red flash)
 */

import { useEffect, useMemo, useState } from "react";

import type { OrchestratorEvent } from "@/api/orchestrator";


interface NodeDef {
  id:     string;
  label:  string;
  hint:   string;
  x:      number;
  y:      number;
}


// 10-node layout mirroring the desktop diagram.
const NODES: NodeDef[] = [
  { id: "goal",     label: "Goal",       hint: "user goal",            x:  60, y: 160 },
  { id: "judge",    label: "LLM Judge",  hint: "Gemini orchestrator",  x: 230, y:  60 },
  { id: "policy",   label: "Policy",     hint: "allowlist + budget",   x: 230, y: 260 },
  { id: "tools",    label: "Tools",      hint: "read-only registry",   x: 410, y: 160 },
  { id: "state",    label: "State",      hint: "state/runs + features",x: 590, y:  60 },
  { id: "reports",  label: "Reports",    hint: "report.md + summary",  x: 590, y: 160 },
  { id: "features", label: "Features",   hint: "feature_list.json",    x: 590, y: 260 },
  { id: "trace",    label: "Trace",      hint: "trace.jsonl",          x: 770, y: 160 },
  { id: "final",    label: "Final",      hint: "final_report.md",      x: 940, y: 160 },
];


// Edges drive both the static SVG lines and the per-tool animation
// (which path a particle travels on a given tool call).
const EDGES: Array<[string, string]> = [
  ["goal", "judge"],
  ["judge", "policy"],
  ["policy", "tools"],
  ["tools", "state"],
  ["tools", "reports"],
  ["tools", "features"],
  ["state", "trace"],
  ["reports", "trace"],
  ["features", "trace"],
  ["trace", "judge"],          // observations flow back to the LLM
  ["judge", "final"],
];


// Which node a given tool name primarily reads from.
const TOOL_TARGET: Record<string, string> = {
  inspect_state:              "state",
  read_latest_report:         "reports",
  read_benchmark_metrics:     "reports",
  compare_runs:               "reports",
  inspect_failed_candidates:  "reports",
  summarize_feature_list:     "features",
  recommend_next_eval:        "features",
};


export interface FlowState {
  nodeStatus:  Record<string, "idle" | "active" | "done" | "error">;
  stepCount:   number;
  finished:    boolean;
}


export function makeInitialFlowState(): FlowState {
  return {
    nodeStatus: Object.fromEntries(NODES.map((n) => [n.id, "idle"])) as Record<
      string, "idle" | "active" | "done" | "error"
    >,
    stepCount: 0,
    finished:  false,
  };
}


/** Reduce one event into the next flow state.
 *
 * Pure — tests can replay a trace.jsonl through this and snapshot the
 * intermediate states without rendering.
 */
export function reduceFlow(prev: FlowState, ev: OrchestratorEvent): FlowState {
  const next: FlowState = {
    nodeStatus: { ...prev.nodeStatus },
    stepCount:  prev.stepCount,
    finished:   prev.finished,
  };
  switch (ev.type) {
    case "session.start":
      next.nodeStatus.goal   = "done";
      next.nodeStatus.judge  = "active";
      next.nodeStatus.policy = "idle";
      break;
    case "llm.turn":
      next.nodeStatus.judge  = "active";
      next.stepCount         = prev.stepCount + 1;
      break;
    case "policy.refuse":
      next.nodeStatus.policy = "error";
      break;
    case "tool.dispatch": {
      const tool = String(ev.tool ?? "");
      next.nodeStatus.policy = "done";
      next.nodeStatus.tools  = "active";
      const target = TOOL_TARGET[tool];
      if (target) next.nodeStatus[target] = "active";
      break;
    }
    case "tool.result": {
      const ok   = ev.ok !== false;
      const tool = String(ev.tool ?? "");
      next.nodeStatus.tools = ok ? "done" : "error";
      const target = TOOL_TARGET[tool];
      if (target) next.nodeStatus[target] = ok ? "done" : "error";
      next.nodeStatus.trace = "active";
      break;
    }
    case "session.final":
      next.nodeStatus.final = "active";
      break;
    case "session.end":
      next.nodeStatus.final = "done";
      next.nodeStatus.judge = "done";
      next.finished = true;
      break;
    case "llm.error":
    case "llm.parse_error":
      next.nodeStatus.judge = "error";
      break;
  }
  return next;
}


export function AgentFlowDiagram({
  lastEvent, flow,
}: {
  lastEvent: OrchestratorEvent | null;
  flow:      FlowState;
}) {
  // Brief particle on the edge of the most recent tool dispatch.
  const [pulseFrom, setPulseFrom] = useState<string | null>(null);
  const [pulseTo,   setPulseTo]   = useState<string | null>(null);
  const [pulseKey,  setPulseKey]  = useState(0);

  useEffect(() => {
    if (!lastEvent) return;
    if (lastEvent.type === "tool.dispatch") {
      const tool = String(lastEvent.tool ?? "");
      const to = TOOL_TARGET[tool];
      if (to) {
        setPulseFrom("tools");
        setPulseTo(to);
        setPulseKey((k) => k + 1);
      }
    } else if (lastEvent.type === "tool.result") {
      const tool = String(lastEvent.tool ?? "");
      const from = TOOL_TARGET[tool];
      if (from) {
        setPulseFrom(from);
        setPulseTo("trace");
        setPulseKey((k) => k + 1);
      }
    }
  }, [lastEvent]);

  // SVG geometry — fixed viewBox so the layout scales with the container.
  const WIDTH = 1020, HEIGHT = 340;
  const byId = useMemo(() =>
    Object.fromEntries(NODES.map((n) => [n.id, n])), []);
  const pulseFromNode = pulseFrom ? byId[pulseFrom] : null;
  const pulseToNode   = pulseTo   ? byId[pulseTo]   : null;

  return (
    <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
         className="w-full" preserveAspectRatio="xMidYMid meet"
         style={{ maxHeight: 360 }}>
      <defs>
        <filter id="agentGlow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      {/* Edges */}
      {EDGES.map(([a, b]) => {
        const A = byId[a], B = byId[b];
        return (
          <line key={`${a}-${b}`}
                x1={A.x + 60} y1={A.y}
                x2={B.x - 60} y2={B.y}
                stroke="rgba(255,255,255,0.18)"
                strokeWidth={1.4}
                strokeDasharray={a === "trace" && b === "judge" ? "4 4" : undefined} />
        );
      })}

      {/* Particle (animated via SMIL when both endpoints exist).
          ``key`` re-mounts the element on every new dispatch, restarting
          the animation. */}
      {pulseFromNode && pulseToNode && (
        <circle key={pulseKey}
                r={5}
                fill="rgba(127,196,255,0.95)"
                filter="url(#agentGlow)">
          <animate attributeName="cx"
                   from={pulseFromNode.x + 60}
                   to={pulseToNode.x - 60}
                   dur="0.7s"
                   repeatCount="1"
                   fill="freeze" />
          <animate attributeName="cy"
                   from={pulseFromNode.y}
                   to={pulseToNode.y}
                   dur="0.7s"
                   repeatCount="1"
                   fill="freeze" />
          <animate attributeName="opacity"
                   from="0.95" to="0" begin="0.5s"
                   dur="0.2s" fill="freeze" />
        </circle>
      )}

      {/* Nodes */}
      {NODES.map((n) => {
        const status = flow.nodeStatus[n.id] ?? "idle";
        const palette = NODE_COLOURS[status];
        return (
          <g key={n.id}>
            <rect x={n.x - 60} y={n.y - 22}
                  width={120} height={44} rx={22}
                  fill={palette.fill}
                  stroke={palette.stroke}
                  strokeWidth={status === "active" ? 2 : 1}
                  filter={status === "active" ? "url(#agentGlow)" : undefined} />
            <text x={n.x} y={n.y - 2}
                  fontSize="13" fontFamily="ui-sans-serif"
                  textAnchor="middle"
                  fill="rgba(255,255,255,0.92)">
              {n.label}
            </text>
            <text x={n.x} y={n.y + 14}
                  fontSize="9.5" fontFamily="ui-sans-serif"
                  textAnchor="middle"
                  fill="rgba(255,255,255,0.55)">
              {n.hint}
            </text>
          </g>
        );
      })}
    </svg>
  );
}


const NODE_COLOURS: Record<
  "idle" | "active" | "done" | "error",
  { fill: string; stroke: string }
> = {
  idle:   { fill: "rgba(20,30,40,0.55)", stroke: "rgba(255,255,255,0.22)" },
  active: { fill: "rgba(40,80,140,0.75)", stroke: "rgba(127,196,255,0.95)" },
  done:   { fill: "rgba(28,52,40,0.6)",   stroke: "rgba(120,200,140,0.7)" },
  error:  { fill: "rgba(70,30,40,0.7)",   stroke: "rgba(240,120,120,0.95)" },
};
