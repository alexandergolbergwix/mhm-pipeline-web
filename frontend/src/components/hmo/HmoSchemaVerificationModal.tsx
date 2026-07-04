import {useCallback, useEffect, useMemo, useRef, useState} from "react";

import type {AiVerdict} from "@/api/extractionApprovals";
import {
  HmoSchemaVerify,
  verdictFromAgentEvent,
} from "@/api/hmoSchemaVerify";
import type {AgentActionMeta, AgentEvent} from "@/api/wikidataVerify";
import {
  AgentFlowDiagram,
  makeInitialFlowState,
  type FlowState,
} from "@/components/AgentFlowDiagram";
import {VerdictsTable} from "@/components/VerdictsTable";
import {Glass} from "@/components/glass";


export interface HmoSchemaVerificationModalProps {
  runId: string;
  ontologyUris?: string[];
  scopeLabel: string;
  onClose: () => void;
  onVerdictsLanded?: (verdicts: Record<string, AiVerdict>) => void;
}


export function HmoSchemaVerificationModal({
  runId,
  ontologyUris,
  scopeLabel,
  onClose,
  onVerdictsLanded,
}: HmoSchemaVerificationModalProps) {
  const [actions, setActions] = useState<AgentActionMeta[]>([]);
  const [actionId, setActionId] = useState("audit_schema_entry");
  const [overrideCache, setOverrideCache] = useState(false);
  const [running, setRunning] = useState(false);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [verdicts, setVerdicts] = useState<Record<string, AgentEvent>>({});
  const [flow, setFlow] = useState<FlowState>(makeInitialFlowState());
  const [error, setError] = useState<string | null>(null);
  const cancelRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    let cancelled = false;
    void HmoSchemaVerify.actions("selection")
      .then((list) => {
        if (cancelled) return;
        setActions(list);
        if (list.length > 0) setActionId(list[0].id);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => { cancelled = true; };
  }, []);

  const action = useMemo(
    () => actions.find((a) => a.id === actionId) ?? null,
    [actions, actionId],
  );

  const emitVerdicts = useCallback((next: Record<string, AgentEvent>) => {
    const mapped: Record<string, AiVerdict> = {};
    for (const ev of Object.values(next)) {
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
    onVerdictsLanded?.(mapped);
  }, [onVerdictsLanded]);

  async function start(): Promise<void> {
    if (running) return;
    setError(null);
    setEvents([]);
    setVerdicts({});
    setFlow(makeInitialFlowState());
    setRunning(true);
    const {events: stream, cancel} = HmoSchemaVerify.stream({
      run_id: runId,
      action_id: actionId,
      ontology_uris: ontologyUris,
      override_cache: overrideCache,
    });
    cancelRef.current = cancel;
    const collected: Record<string, AgentEvent> = {};
    try {
      for await (const ev of stream) {
        setEvents((prev) => [...prev, ev]);
        if (ev.type === "agent.stats") {
          const payload = (ev.payload ?? ev) as Record<string, unknown>;
          setFlow((prev) => ({
            ...prev,
            judged: Number(payload.judged ?? prev.judged),
            total: Number(payload.total ?? prev.total),
          }));
        }
        if (ev.type === "agent.verdict") {
          const parsed = verdictFromAgentEvent(ev);
          if (parsed) {
            collected[parsed.localId] = ev;
            setVerdicts({...collected});
          }
        }
      }
      emitVerdicts(collected);
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setRunning(false);
      cancelRef.current = null;
    }
  }

  function stop(): void {
    cancelRef.current?.();
    setRunning(false);
  }

  const lastEvent = events.length > 0 ? events[events.length - 1] : null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-stretch justify-center bg-black/60 backdrop-blur-md p-4 md:p-6"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <Glass variant="modal" className="max-w-7xl w-full max-h-full overflow-auto p-6 space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="kicker">AI verification · HMO schema · {scopeLabel}</div>
            <h3 className="text-xl font-semibold">Verify schema entries with AI</h3>
            {action && <p className="muted text-sm max-w-2xl">{action.description}</p>}
          </div>
          <button onClick={onClose} className="button-ghost !py-1 !px-3 text-sm" title="Close">
            ✕
          </button>
        </div>

        <div className="flex flex-wrap items-end gap-3 pt-1">
          <div className="flex flex-col gap-1 text-xs">
            <label className="muted">Action</label>
            <select
              value={actionId}
              onChange={(e) => setActionId(e.target.value)}
              disabled={running || actions.length === 0}
              className="input-glass !py-1 text-sm min-w-[260px]"
            >
              {actions.map((a) => (
                <option key={a.id} value={a.id}>{a.label}</option>
              ))}
            </select>
          </div>
          <label className="muted text-xs flex items-center gap-2">
            <input
              type="checkbox"
              checked={overrideCache}
              onChange={(e) => setOverrideCache(e.target.checked)}
              disabled={running}
            />
            <span>Override cache (force fresh LLM call)</span>
          </label>
          {!running ? (
            <button onClick={() => { void start(); }} disabled={!action} className="button-primary text-sm">
              Start verification
            </button>
          ) : (
            <button onClick={stop} className="button-ghost text-sm text-warn">
              Stop
            </button>
          )}
          {error && <span className="text-danger text-xs">{error}</span>}
        </div>

        <AgentFlowDiagram flow={flow} lastEvent={lastEvent} />

        <VerdictsTable verdicts={verdicts} />
      </Glass>
    </div>
  );
}
