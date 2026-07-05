import {useCallback, useEffect, useRef, useState} from "react";

import type {AiVerdict} from "@/api/extractionApprovals";
import {HmoItemVerify, verdictFromAgentEvent} from "@/api/hmoItemVerify";
import type {AgentActionMeta, AgentEvent} from "@/api/wikidataVerify";
import {makeInitialFlowState, reduceFlow, type FlowState} from "@/components/AgentFlowDiagram";

function mapVerdictEvents(events: Record<string, AgentEvent>): Record<string, AiVerdict> {
  const mapped: Record<string, AiVerdict> = {};
  for (const ev of Object.values(events)) {
    const parsed = verdictFromAgentEvent(ev);
    if (!parsed) continue;
    mapped[parsed.localId] = {
      overall: parsed.overall as AiVerdict["overall"],
      name_ok: null,
      type_ok: null,
      role_ok: null,
      reasoning: parsed.reasoning ?? null,
      model: null,
      judged_at: null,
      cache_key: null,
      session_id: null,
      evaluator: "hmo_wikibase_item",
    };
  }
  return mapped;
}

export interface HmoItemVerifySession {
  actions: AgentActionMeta[];
  actionId: string;
  setActionId: (id: string) => void;
  overrideCache: boolean;
  setOverrideCache: (v: boolean) => void;
  running: boolean;
  events: AgentEvent[];
  verdicts: Record<string, AgentEvent>;
  flow: FlowState;
  error: string | null;
  warning: string | null;
  doneMessage: string | null;
  start: (runId: string, itemIds?: string[]) => void;
  stop: () => void;
}

export function useHmoItemVerifySession(
  runId: string,
  onVerdictsLanded?: (verdicts: Record<string, AiVerdict>) => void,
): HmoItemVerifySession {
  const [actions, setActions] = useState<AgentActionMeta[]>([]);
  const [actionId, setActionId] = useState("audit_hmo_wikibase_item");
  const [overrideCache, setOverrideCache] = useState(false);
  const [running, setRunning] = useState(false);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [verdicts, setVerdicts] = useState<Record<string, AgentEvent>>({});
  const [flow, setFlow] = useState<FlowState>(makeInitialFlowState());
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [doneMessage, setDoneMessage] = useState<string | null>(null);
  const cancelRef = useRef<(() => void) | null>(null);
  const onVerdictsLandedRef = useRef(onVerdictsLanded);
  onVerdictsLandedRef.current = onVerdictsLanded;

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    void HmoItemVerify.actions(runId, "selection")
      .then((list) => {
        if (cancelled) return;
        setActions(list);
        setActionId((prev) => (prev ? prev : (list[0]?.id ?? prev)));
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => { cancelled = true; };
  }, [runId]);

  const pushVerdictsToParent = useCallback((next: Record<string, AgentEvent>) => {
    const mapped = mapVerdictEvents(next);
    if (Object.keys(mapped).length > 0) {
      onVerdictsLandedRef.current?.(mapped);
    }
  }, []);

  const start = useCallback((runId: string, itemIds?: string[]) => {
    if (running) return;
    setError(null);
    setWarning(null);
    setDoneMessage(null);
    setEvents([]);
    setVerdicts({});
    setFlow(makeInitialFlowState());
    setRunning(true);

    const {events: stream, cancel} = HmoItemVerify.stream(runId, {
      action_id: actionId,
      item_ids: itemIds,
      override_cache: overrideCache,
    });
    cancelRef.current = cancel;
    const collected: Record<string, AgentEvent> = {};

    void (async () => {
      try {
        for await (const ev of stream) {
          setEvents((prev) => [...prev, ev]);
          setFlow((prev) => reduceFlow(prev, ev));
          if (ev.type === "runner.warning") {
            const payload = (ev.payload ?? ev) as Record<string, unknown>;
            setWarning(typeof payload.message === "string" ? payload.message : "Verification warning");
          }
          if (ev.type === "agent.verdict") {
            const parsed = verdictFromAgentEvent(ev);
            if (parsed) {
              collected[parsed.localId] = ev;
              const next = {...collected};
              setVerdicts(next);
              pushVerdictsToParent(next);
            }
          }
          if (ev.type === "session.end") {
            setDoneMessage("Verification complete.");
          }
        }
      } catch (e) {
        if ((e as Error).name !== "AbortError") {
          setError(e instanceof Error ? e.message : String(e));
        }
      } finally {
        setRunning(false);
        cancelRef.current = null;
      }
    })();
  }, [actionId, overrideCache, pushVerdictsToParent, running]);

  const stop = useCallback(() => {
    cancelRef.current?.();
    setRunning(false);
  }, []);

  return {
    actions,
    actionId,
    setActionId,
    overrideCache,
    setOverrideCache,
    running,
    events,
    verdicts,
    flow,
    error,
    warning,
    doneMessage,
    start,
    stop,
  };
}
