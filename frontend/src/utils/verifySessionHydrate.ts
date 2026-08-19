import type {AgentEvent} from "@/api/aiVerify";
import type {RunJobProgress} from "@/api/runJobs";
import {makeInitialFlowState, reduceFlow, type FlowState} from "@/components/AgentFlowDiagram";

const FLOW_EVENT_TYPES = new Set([
  "session.start",
  "agent.stats",
  "runner.step",
  "agent.verdict",
  "session.end",
  "llm.error",
  "runner.error",
]);

export function traceLineToAgentEvent(line: Record<string, unknown>): AgentEvent {
  const type = String(line.type ?? "message");
  const rest = {...line};
  delete rest.type;
  delete rest.ts;
  return {type, ...rest};
}

export interface HydratedVerifySession {
  events: AgentEvent[];
  verdicts: Record<string, AgentEvent>;
  flow: FlowState;
}

export function hydrateVerifySession(
  full: {events?: unknown[]; verdicts?: Array<Record<string, unknown>>},
  keyOf: (row: Record<string, unknown>) => string,
): HydratedVerifySession {
  const traceEvents: AgentEvent[] = [];
  let flow = makeInitialFlowState();

  for (const raw of full.events ?? []) {
    if (!raw || typeof raw !== "object") continue;
    const ev = traceLineToAgentEvent(raw as Record<string, unknown>);
    if (!FLOW_EVENT_TYPES.has(ev.type)) continue;
    traceEvents.push(ev);
    flow = reduceFlow(flow, ev);
  }

  const verdicts: Record<string, AgentEvent> = {};
  for (const row of full.verdicts ?? []) {
    const key = keyOf(row);
    if (!key) continue;
    verdicts[key] = {type: "agent.verdict", ...row};
  }

  if (traceEvents.length === 0 && Object.keys(verdicts).length > 0) {
    flow = reduceFlow(makeInitialFlowState(), {
      type: "session.start",
      scope_size: Object.keys(verdicts).length,
    });
    for (const ev of Object.values(verdicts)) {
      flow = reduceFlow(flow, ev);
    }
    return {events: Object.values(verdicts), verdicts, flow};
  }

  if (
    traceEvents.length > 0
    && !traceEvents.some((ev) => ev.type === "session.start")
  ) {
    const scope = Math.max(
      flow.total,
      Object.keys(verdicts).length,
      traceEvents.filter((ev) => ev.type === "agent.verdict").length,
    );
    let rebuilt = reduceFlow(makeInitialFlowState(), {
      type: "session.start",
      scope_size: scope,
    });
    for (const ev of traceEvents) {
      if (ev.type === "session.start") continue;
      rebuilt = reduceFlow(rebuilt, ev);
    }
    flow = rebuilt;
  }

  return {events: traceEvents, verdicts, flow};
}

/** Light up the diagram from background-job progress when trace is not yet readable. */
export function applyJobProgressToFlow(
  prev: FlowState,
  progress: RunJobProgress,
): FlowState {
  const total = Number(progress.total ?? 0);
  const processed = Number(progress.processed ?? 0);
  if (total <= 0 && processed <= 0) return prev;

  const next: FlowState = {
    ...prev,
    nodeStatus: {...prev.nodeStatus},
    total: total > 0 ? total : prev.total,
    judged: Math.max(prev.judged, processed),
    lastEdge: prev.lastEdge,
  };

  next.nodeStatus.inputs = "done";
  next.nodeStatus.rubric = "done";
  next.nodeStatus.candidates = "done";

  const phase = String(progress.phase ?? "");
  const lastType = String(progress.last_event_type ?? "");

  if (phase === "done") {
    next.nodeStatus.cache = "done";
    next.nodeStatus.judge = "done";
    next.nodeStatus.verdict = "done";
    next.nodeStatus.report = "done";
    next.finished = true;
    next.lastEdge = ["verdict", "report"];
    return next;
  }

  if (lastType === "session.start" && processed === 0) {
    next.nodeStatus.cache = "active";
    next.lastEdge = ["candidates", "cache"];
    return next;
  }

  next.nodeStatus.cache = "done";
  if (total > 0 && processed >= total) {
    next.nodeStatus.judge = "done";
    next.nodeStatus.verdict = "active";
    next.lastEdge = ["judge", "verdict"];
  } else {
    next.nodeStatus.judge = "active";
    next.lastEdge = ["cache", "judge"];
  }

  return next;
}

export function mergeFlowWithJobProgress(
  flow: FlowState,
  progress: RunJobProgress | null | undefined,
): FlowState {
  if (!progress) return flow;
  const merged = applyJobProgressToFlow(flow, progress);
  const cacheHits = Math.max(
    flow.cacheHits,
    merged.cacheHits,
    Number(progress.cache_hits ?? 0),
  );
  return {
    ...merged,
    judged: Math.max(flow.judged, merged.judged),
    total: Math.max(flow.total, merged.total),
    cacheHits,
    finished: flow.finished || merged.finished,
  };
}
