import {useCallback, useEffect, useRef, useState} from "react";

import type {AiVerdict} from "@/api/extractionApprovals";
import {HmoSchemaVerify, verdictFromAgentEvent} from "@/api/hmoSchemaVerify";
import type {AgentActionMeta, AgentEvent} from "@/api/wikidataVerify";
import {makeInitialFlowState, reduceFlow, type FlowState} from "@/components/AgentFlowDiagram";
import {useTier1Model} from "@/components/Tier1ModelSelect";

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
      reasoning: parsed.reasoning,
      model: null,
      judged_at: null,
      cache_key: null,
      session_id: null,
      evaluator: "hmo_wikibase_schema",
    };
  }
  return mapped;
}

export interface HmoSchemaVerifySession {
  actions: AgentActionMeta[];
  actionId: string;
  setActionId: (id: string) => void;
  overrideCache: boolean;
  setOverrideCache: (v: boolean) => void;
  tierModel: string;
  setTierModel: (v: string) => void;
  tier1List: ReturnType<typeof useTier1Model>["list"];
  tier1Loading: boolean;
  running: boolean;
  events: AgentEvent[];
  verdicts: Record<string, AgentEvent>;
  flow: FlowState;
  error: string | null;
  warning: string | null;
  doneMessage: string | null;
  start: (runId: string, ontologyUris?: string[]) => void;
  stop: () => void;
}

/**
 * Owns the AI-verify SSE stream independently of whether the review
 * modal is mounted. Call this hook once in the panel (which stays
 * mounted for the whole page visit) and pass the returned session down
 * as a controlled prop — closing the modal only hides the UI, it never
 * cancels the in-flight verification, so it keeps landing verdicts in
 * the background and the modal picks up right where it left off if
 * reopened.
 */
export function useHmoSchemaVerifySession(
  onVerdictsLanded?: (verdicts: Record<string, AiVerdict>) => void,
): HmoSchemaVerifySession {
  const [actions, setActions] = useState<AgentActionMeta[]>([]);
  const [actionId, setActionId] = useState("audit_schema_entry");
  const [overrideCache, setOverrideCache] = useState(false);
  const [running, setRunning] = useState(false);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [verdicts, setVerdicts] = useState<Record<string, AgentEvent>>({});
  const [flow, setFlow] = useState<FlowState>(makeInitialFlowState());
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [doneMessage, setDoneMessage] = useState<string | null>(null);
  const {list: tier1List, tierModel, setTierModel, loading: tier1Loading} = useTier1Model();
  const cancelRef = useRef<(() => void) | null>(null);
  const onVerdictsLandedRef = useRef(onVerdictsLanded);
  onVerdictsLandedRef.current = onVerdictsLanded;

  useEffect(() => {
    let cancelled = false;
    void HmoSchemaVerify.actions("selection")
      .then((list) => {
        if (cancelled) return;
        setActions(list);
        setActionId((prev) => (prev ? prev : (list[0]?.id ?? prev)));
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => { cancelled = true; };
  }, []);

  const pushVerdictsToParent = useCallback((next: Record<string, AgentEvent>) => {
    const mapped = mapVerdictEvents(next);
    if (Object.keys(mapped).length > 0) {
      onVerdictsLandedRef.current?.(mapped);
    }
  }, []);

  const start = useCallback((runId: string, ontologyUris?: string[]) => {
    if (running) return;
    setError(null);
    setWarning(null);
    setDoneMessage(null);
    setEvents([]);
    setVerdicts({});
    setFlow(makeInitialFlowState());
    setRunning(true);
    const {events: stream, cancel} = HmoSchemaVerify.stream({
      run_id: runId,
      action_id: actionId,
      ontology_uris: ontologyUris,
      override_cache: overrideCache,
      tier_model: tierModel,
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
            const msg = typeof payload.message === "string"
              ? payload.message
              : "The eval-agent is not available on this server.";
            setWarning(msg);
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
            const payload = (ev.payload ?? ev) as Record<string, unknown>;
            const fresh = Number(payload.fresh_verdicts ?? 0);
            const cached = Number(payload.cache_hits ?? 0);
            const skipped = Number(payload.uncached_skipped ?? 0);
            const outcome = String(payload.outcome ?? "complete");
            const parts: string[] = [];
            if (fresh > 0) parts.push(`${fresh} fresh`);
            if (cached > 0) parts.push(`${cached} cached`);
            if (skipped > 0) parts.push(`${skipped} skipped (no agent)`);
            if (parts.length === 0 && outcome === "partial") {
              setDoneMessage("Verification finished with no verdicts — see warning above.");
            } else if (parts.length > 0) {
              setDoneMessage(`Verification complete: ${parts.join(" · ")}.`);
            } else {
              setDoneMessage("Verification complete.");
            }
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
  }, [running, actionId, overrideCache, tierModel, pushVerdictsToParent]);

  const stop = useCallback(() => {
    cancelRef.current?.();
    setRunning(false);
  }, []);

  return {
    actions, actionId, setActionId,
    overrideCache, setOverrideCache,
    tierModel, setTierModel, tier1List, tier1Loading,
    running, events, verdicts, flow,
    error, warning, doneMessage,
    start, stop,
  };
}
