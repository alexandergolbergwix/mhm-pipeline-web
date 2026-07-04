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
  reduceFlow,
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
  const [warning, setWarning] = useState<string | null>(null);
  const [doneMessage, setDoneMessage] = useState<string | null>(null);
  const cancelRef = useRef<(() => void) | null>(null);
  const onVerdictsLandedRef = useRef(onVerdictsLanded);
  onVerdictsLandedRef.current = onVerdictsLanded;

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

  const pushVerdictsToParent = useCallback((next: Record<string, AgentEvent>) => {
    const mapped = mapVerdictEvents(next);
    if (Object.keys(mapped).length > 0) {
      onVerdictsLandedRef.current?.(mapped);
    }
  }, []);

  async function start(): Promise<void> {
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
    });
    cancelRef.current = cancel;
    const collected: Record<string, AgentEvent> = {};
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
            setDoneMessage(`Verification complete: ${parts.join(" · ")}. Close this window when done reviewing.`);
          } else {
            setDoneMessage("Verification complete. Close this window when done reviewing.");
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
  }

  function stop(): void {
    cancelRef.current?.();
    setRunning(false);
  }

  const lastEvent = events.length > 0 ? events[events.length - 1] : null;
  const verdictCount = Object.keys(verdicts).length;

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
            <p className="muted text-xs mt-1 max-w-2xl">
              Only entries that would be created (or failed on a live run) are judged —
              already-mapped rows are skipped by design.
            </p>
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
          {verdictCount > 0 && (
            <span className="text-xs muted">{verdictCount} verdict{verdictCount === 1 ? "" : "s"}</span>
          )}
          {error && <span className="text-danger text-xs">{error}</span>}
        </div>

        {warning && (
          <div className="text-xs text-warn px-3 py-2 rounded-lg border border-warn/30 bg-warn/10">
            {warning}
          </div>
        )}

        {doneMessage && !running && (
          <div className="text-xs text-ink/90 px-3 py-2 rounded-lg border border-biu-sky/25 bg-biu-sky/8">
            {doneMessage}
          </div>
        )}

        <AgentFlowDiagram flow={flow} lastEvent={lastEvent} />

        <VerdictsTable verdicts={verdicts} />

        <Glass as="details" variant="compact" className="p-3">
          <summary className="kicker cursor-pointer hover:text-ink">
            Step log ({events.length})
          </summary>
          <ul className="space-y-1 text-[11px] font-mono max-h-48 overflow-auto mt-2">
            {events.length === 0 && <li className="muted italic">Waiting for the first event…</li>}
            {events.slice(-80).map((ev, i) => (
              <li key={i} className="flex gap-2">
                <span className="muted shrink-0 w-28">{(ev.type ?? "message").slice(0, 18).padEnd(18)}</span>
                <span className="text-ink/90 truncate">{ev.type}</span>
              </li>
            ))}
          </ul>
        </Glass>
      </Glass>
    </div>
  );
}
