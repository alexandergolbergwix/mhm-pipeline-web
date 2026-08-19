/**
 * AgentFlowDiagram — honest visualisation of the per-candidate judge.
 *
 * The web app invokes ``eval_agent.cli run``, which is the per-candidate
 * judging loop (NOT the higher-level orchestrator). The diagram shows
 * the surfaces that loop actually touches:
 *
 *   INPUTS  → CANDIDATES → CACHE  ─(hit)─→  VERDICT → REPORT
 *                          │
 *   RUBRIC ─────────────────┘
 *                          │
 *                          └(miss)→ JUDGE ─→ TOOLS ─┐
 *                                     │            │
 *                                     └─→ ESCALATE ┘
 *                                                  ↓
 *                                               VERDICT
 *
 * Every node maps to a concrete event from the eval-agent subprocess:
 *
 *   session.start    → INPUTS + RUBRIC + CANDIDATES
 *   agent.stats      → CACHE (hits++) or JUDGE (judged++)
 *   runner.step      → TOOLS (msg starts "tool ") or ESCALATE (msg
 *                       starts "escalate")
 *   agent.verdict    → VERDICT
 *   session.end      → REPORT
 *
 * Nothing in here corresponds to the orchestrator's policy/state/tools
 * registry surface — that's a different subcommand we don't invoke from
 * the modal. Showing those nodes here would be theatre.
 */

import { useEffect, useMemo, useState } from "react";

import type { AgentEvent } from "@/api/aiVerify";


interface NodeDef {
  id:     string;
  label:  string;
  hint:   string;
  x:      number;
  y:      number;
}


// 9 nodes — every one corresponds to an event we actually receive from
// the eval-agent `run` subprocess.
export type AgentFlowVariant = "default" | "wikidata";

const VARIANT_HINTS: Record<AgentFlowVariant, Partial<Record<string, string>>> = {
  default: {},
  wikidata: {
    inputs:     "marc + studio items",
    rubric:     "wikidata_item.md",
    candidates: "per studio item",
    cache:      "inference_cache",
    verdict:    "labels + statements",
  },
};
const NODES: NodeDef[] = [
  { id: "inputs",     label: "Inputs",     hint: "marc + authority",    x:  70, y:  60 },
  { id: "rubric",     label: "Rubric",     hint: "config/rubrics/*.md", x:  70, y: 260 },
  { id: "candidates", label: "Candidates", hint: "per evaluator",       x: 250, y: 160 },
  { id: "cache",      label: "Cache",      hint: "verdict_cache.jsonl", x: 430, y: 160 },
  { id: "judge",      label: "Tier-1 LLM", hint: "Gemini judge",        x: 610, y:  60 },
  { id: "tools",      label: "Tools",      hint: "agentic per-cand.",   x: 610, y: 260 },
  { id: "escalate",   label: "Tier-2 LLM", hint: "abstain → escalate",  x: 790, y: 260 },
  { id: "verdict",    label: "Verdict",    hint: "results.jsonl",       x: 790, y:  60 },
  { id: "report",     label: "Report",     hint: "report.md + csv",     x: 970, y: 160 },
];


// Directed flow. Bidirectional / loop edges expressed as separate pairs.
const EDGES: Array<[string, string, boolean?]> = [
  ["inputs",     "candidates"],
  ["rubric",     "candidates"],
  ["candidates", "cache"],
  ["cache",      "verdict",  true],   // dashed: cache-hit fast path
  ["cache",      "judge"],
  ["judge",      "tools"],
  ["tools",      "judge"],             // agentic loop (round-trip)
  ["judge",      "escalate", true],    // dashed: only on abstain
  ["escalate",   "verdict"],
  ["judge",      "verdict"],
  ["verdict",    "report"],
];


export interface FlowState {
  nodeStatus:  Record<string, "idle" | "active" | "done" | "error">;
  stepCount:   number;
  finished:    boolean;
  cacheHits:   number;
  judged:      number;
  total:       number;
  lastEdge:    [string, string] | null;
}


export function makeInitialFlowState(): FlowState {
  return {
    nodeStatus: Object.fromEntries(NODES.map((n) => [n.id, "idle"])) as Record<
      string, "idle" | "active" | "done" | "error"
    >,
    stepCount: 0,
    finished:  false,
    cacheHits: 0,
    judged:    0,
    total:     0,
    lastEdge:  null,
  };
}


/** Reduce one event into the next flow state.
 *
 * Pure — tests can replay a trace.jsonl through this and snapshot the
 * intermediate states without rendering. */
export function reduceFlow(prev: FlowState, ev: AgentEvent): FlowState {
  const next: FlowState = {
    nodeStatus: { ...prev.nodeStatus },
    stepCount:  prev.stepCount,
    finished:   prev.finished,
    cacheHits:  prev.cacheHits,
    judged:     prev.judged,
    total:      prev.total,
    lastEdge:   null,
  };
  switch (ev.type) {
    case "session.start": {
      next.nodeStatus.inputs     = "done";
      next.nodeStatus.rubric     = "done";
      next.nodeStatus.candidates = "active";
      const scope = Number((ev as { scope_size?: number }).scope_size ?? 0);
      if (scope > 0) next.total = scope;
      const hits = Number((ev as { cache_hits?: number }).cache_hits ?? 0);
      if (hits > 0) {
        next.cacheHits = hits;
        next.nodeStatus.cache = "active";
      }
      break;
    }
    case "agent.stats": {
      const hits   = Number((ev as { hits?: number }).hits ?? prev.cacheHits);
      const judged = Number((ev as { judged?: number }).judged ?? prev.judged);
      const total  = Number((ev as { total?: number }).total ?? prev.total);
      const newHits   = hits   > prev.cacheHits;
      const newJudged = judged > prev.judged;
      next.cacheHits = hits;
      next.judged    = judged;
      if (total > 0) next.total = total;
      next.nodeStatus.candidates = "done";
      if (newHits) {
        next.nodeStatus.cache = "active";
        next.lastEdge = ["cache", "verdict"];
      }
      if (newJudged) {
        next.nodeStatus.cache = "done";
        next.nodeStatus.judge = "active";
        next.lastEdge = ["cache", "judge"];
      }
      break;
    }
    case "runner.step": {
      const msg = String((ev as { message?: string }).message ?? "");
      if (msg.startsWith("tool ")) {
        next.nodeStatus.judge = "done";
        next.nodeStatus.tools = "active";
        next.lastEdge = ["judge", "tools"];
        next.stepCount += 1;
      } else if (msg.startsWith("escalate")) {
        next.nodeStatus.escalate = "active";
        next.lastEdge = ["judge", "escalate"];
        next.stepCount += 1;
      }
      break;
    }
    case "agent.verdict": {
      next.nodeStatus.verdict = "active";
      const cameFromEscalate = prev.nodeStatus.escalate === "active";
      next.lastEdge = cameFromEscalate ? ["escalate", "verdict"] : ["judge", "verdict"];
      if (cameFromEscalate) next.nodeStatus.escalate = "done";
      else                  next.nodeStatus.judge    = "done";
      break;
    }
    case "session.end":
      next.nodeStatus.verdict = "done";
      next.nodeStatus.report  = "done";
      next.finished = true;
      break;
    case "llm.error":
    case "runner.error":
      next.nodeStatus.judge = "error";
      break;
  }
  return next;
}


export function AgentFlowDiagram({
  lastEvent, flow, variant = "default",
}: {
  lastEvent: AgentEvent | null;
  flow:      FlowState;
  variant?:  AgentFlowVariant;
}) {
  // Animate a particle along the most recently lit edge.
  const [pulseFrom, setPulseFrom] = useState<string | null>(null);
  const [pulseTo,   setPulseTo]   = useState<string | null>(null);
  const [pulseKey,  setPulseKey]  = useState(0);

  useEffect(() => {
    if (!lastEvent || !flow.lastEdge) return;
    setPulseFrom(flow.lastEdge[0]);
    setPulseTo(flow.lastEdge[1]);
    setPulseKey((k) => k + 1);
  }, [lastEvent, flow.lastEdge]);

  // SVG geometry — fixed viewBox so the layout scales with the container.
  const WIDTH = 1050, HEIGHT = 340;
  const byId = useMemo(() =>
    Object.fromEntries(NODES.map((n) => [n.id, n])), []);
  const pulseFromNode = pulseFrom ? byId[pulseFrom] : null;
  const pulseToNode   = pulseTo   ? byId[pulseTo]   : null;

  // Stat badges to surface live cache-vs-LLM ratio. Honest, not
  // decorative — these come straight from `agent.stats`.
  const cacheLabel = flow.cacheHits > 0 ? `${flow.cacheHits} hit` : "";
  const judgeLabel = flow.judged    > 0 ? `${flow.judged} judged`  : "";
  const hints = VARIANT_HINTS[variant];

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
      {EDGES.map(([a, b, dashed], i) => {
        const A = byId[a], B = byId[b];
        return (
          <line key={`${a}-${b}-${i}`}
                x1={A.x + 60} y1={A.y}
                x2={B.x - 60} y2={B.y}
                stroke="rgba(255,255,255,0.18)"
                strokeWidth={1.4}
                strokeDasharray={dashed ? "4 4" : undefined} />
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
        const badge = n.id === "cache" ? cacheLabel
                    : n.id === "judge" ? judgeLabel
                    : "";
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
              {hints[n.id] ?? n.hint}
            </text>
            {badge && (
              <text x={n.x} y={n.y + 36}
                    fontSize="10" fontFamily="ui-sans-serif"
                    textAnchor="middle"
                    fill="rgba(127,196,255,0.85)">
                {badge}
              </text>
            )}
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
