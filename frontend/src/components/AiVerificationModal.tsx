/**
 * AiVerificationModal — opens from inside a run page, runs the
 * eval-agent's candidate-level agentic judge over a scope of matches,
 * and shows live progress.
 *
 * Three entry points (single-row, selection, all-filtered) all open
 * this same modal with a different ``matchIds`` array. The modal
 * subscribes to the SSE stream, animates the AgentFlowDiagram, tails
 * the step log, and accumulates per-candidate verdicts into a table.
 *
 * No free-text prompts — the curator picks an action from the
 * server-side registry (audit_match / find_duplicates / birth_death_check).
 * New actions are server-side dict entries, never UI input.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import {
  AiVerify,
  streamVerification,
  type AgentActionMeta,
  type AgentEvent,
  type ScopeKind,
} from "@/api/aiVerify";
import {
  AgentFlowDiagram, makeInitialFlowState, reduceFlow, type FlowState,
} from "@/components/AgentFlowDiagram";


export interface AiVerificationModalProps {
  runId:     string;
  scopeKind: ScopeKind;             // "single" | "selection" | "all"
  matchIds?: string[];              // omitted ⇒ all matches in run
  scopeLabel: string;               // for the header ("12 selected" / "all 84 visible" / "1 match")
  onClose:   () => void;
}


export function AiVerificationModal(props: AiVerificationModalProps) {
  const { runId, scopeKind, matchIds, scopeLabel, onClose } = props;

  const [actions,    setActions]    = useState<AgentActionMeta[]>([]);
  const [actionId,   setActionId]   = useState<string>("audit_match");
  const [useStubLlm, setUseStubLlm] = useState(false);
  const [running,    setRunning]    = useState(false);
  const [events,     setEvents]     = useState<AgentEvent[]>([]);
  const [verdicts,   setVerdicts]   = useState<Record<string, AgentEvent>>({});
  const [flow,       setFlow]       = useState<FlowState>(makeInitialFlowState());
  const [error,      setError]      = useState<string | null>(null);

  const cancelRef = useRef<(() => void) | null>(null);

  // Load actions for this scope kind.
  useEffect(() => {
    AiVerify.listActions(runId, scopeKind)
      .then((list) => {
        setActions(list);
        if (list.length > 0) setActionId(list[0].id);
      })
      .catch((e) => setError((e as Error).message));
  }, [runId, scopeKind]);

  const lastEvent = events.length > 0 ? events[events.length - 1] : null;

  async function start(): Promise<void> {
    if (running) return;
    setRunning(true); setError(null);
    setEvents([]); setVerdicts({}); setFlow(makeInitialFlowState());
    const { events: stream, cancel } = streamVerification(runId, {
      action_id:    actionId,
      match_ids:    matchIds,
      use_no_llm:   useStubLlm,
    });
    cancelRef.current = cancel;
    try {
      for await (const ev of stream) {
        setEvents((p) => [...p, ev]);
        setFlow((p) => reduceFlow(p, ev));
        if (ev.type === "agent.verdict") {
          const candidate = (ev.candidate ?? {}) as Record<string, unknown>;
          const matchId = String(candidate._match_id ?? candidate.record_id ?? "");
          if (matchId) setVerdicts((p) => ({ ...p, [matchId]: ev }));
        }
        if (ev.type === "session.end") break;
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setError((e as Error).message);
      }
    } finally {
      cancelRef.current = null;
      setRunning(false);
    }
  }

  function stop(): void { cancelRef.current?.(); }

  const action = useMemo(
    () => actions.find((a) => a.id === actionId) ?? null,
    [actions, actionId],
  );

  // Stats card.
  const verdictCount   = Object.values(verdicts).length;
  const passCount      = Object.values(verdicts).filter((v) => verdictOverall(v) === "pass" || verdictOverall(v) === "full").length;
  const partialCount   = Object.values(verdicts).filter((v) => verdictOverall(v) === "partial").length;
  const failCount      = Object.values(verdicts).filter((v) => verdictOverall(v) === "fail").length;

  return (
    <div className="fixed inset-0 z-50 flex items-stretch justify-center bg-black/60 backdrop-blur-md p-4 md:p-6"
         onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="glass max-w-7xl w-full max-h-full overflow-auto p-6 space-y-4">
        {/* Header */}
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="kicker">AI verification · {scopeLabel}</div>
            <h3 className="text-xl font-semibold">Verify with AI agent</h3>
            {action && <p className="muted text-sm max-w-2xl">{action.description}</p>}
          </div>
          <button onClick={onClose} className="button-ghost !py-1 !px-3 text-sm" title="Close">
            ✕
          </button>
        </div>

        {/* Action picker + controls */}
        <div className="flex flex-wrap items-end gap-3 pt-1">
          <div className="flex flex-col gap-1 text-xs">
            <label className="muted">Action</label>
            <select value={actionId} onChange={(e) => setActionId(e.target.value)}
                    disabled={running || actions.length === 0}
                    className="input-glass !py-1 text-sm min-w-[260px]">
              {actions.length === 0 && <option>Loading…</option>}
              {actions.map((a) => (
                <option key={a.id} value={a.id}>{a.label}</option>
              ))}
            </select>
          </div>
          <label className="muted text-xs flex items-center gap-2">
            <input type="checkbox" checked={useStubLlm}
                   onChange={(e) => setUseStubLlm(e.target.checked)}
                   disabled={running} />
            <span>Stub judge (no LLM — demo)</span>
          </label>
          {!running
            ? <button onClick={start} disabled={!action}
                      className="button-primary text-sm">
                Start verification
              </button>
            : <button onClick={stop} className="button-ghost text-sm text-yellow-300">
                Stop
              </button>}
          {error && <span className="text-red-300 text-xs">{error}</span>}
        </div>

        {/* Live diagram */}
        <section className="glass-pill p-3">
          <div className="kicker mb-2">Agent flow</div>
          <AgentFlowDiagram lastEvent={lastEvent} flow={flow} />
        </section>

        {/* Two-column body: verdicts left, step log right */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Verdicts */}
          <section className="glass-pill p-3">
            <div className="flex items-baseline justify-between mb-2">
              <div className="kicker">Verdicts ({verdictCount})</div>
              <div className="text-[11px] flex gap-2">
                <span className="text-biu-sky">{passCount} pass</span>
                <span className="text-yellow-300">{partialCount} partial</span>
                <span className="text-red-300">{failCount} fail</span>
              </div>
            </div>
            <ul className="space-y-1 text-xs max-h-72 overflow-auto">
              {verdictCount === 0 && <li className="muted italic">Waiting for verdicts…</li>}
              {Object.entries(verdicts).map(([id, v]) => (
                <li key={id} className="flex gap-2 items-baseline">
                  <VerdictPill overall={verdictOverall(v)} />
                  <span className="flex-1 truncate" title={verdictReasoning(v)}>
                    {verdictCandidateName(v)}
                  </span>
                  <span className="muted text-[10px]">{verdictRole(v)}</span>
                </li>
              ))}
            </ul>
          </section>

          {/* Step log */}
          <section className="glass-pill p-3">
            <div className="kicker mb-2">Step log ({events.length})</div>
            <ul className="space-y-1 text-[11px] font-mono max-h-72 overflow-auto">
              {events.length === 0 && <li className="muted italic">Waiting for the first event…</li>}
              {events.slice(-80).map((ev, i) => (
                <li key={i} className="flex gap-2">
                  <span className="muted shrink-0 w-28">{(ev.type as string).slice(0, 18).padEnd(18)}</span>
                  <span className="text-ink/90 truncate">{stepLogLine(ev)}</span>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </div>
    </div>
  );
}


// ── Helpers ───────────────────────────────────────────────────────────


function verdictOverall(ev: AgentEvent): string {
  const v = (ev.verdict ?? ev.candidate ?? {}) as Record<string, unknown>;
  return String(v.overall ?? "").toLowerCase() || "unknown";
}


function verdictCandidateName(ev: AgentEvent): string {
  const c = (ev.candidate ?? {}) as Record<string, unknown>;
  return String(c.name ?? c.text ?? c.entity_text ?? c.record_id ?? "(unknown)");
}


function verdictRole(ev: AgentEvent): string {
  const c = (ev.candidate ?? {}) as Record<string, unknown>;
  return String(c.role ?? c.sub_type ?? "");
}


function verdictReasoning(ev: AgentEvent): string {
  const v = (ev.verdict ?? {}) as Record<string, unknown>;
  return String(v.reasoning ?? v.note ?? "");
}


function VerdictPill({ overall }: { overall: string }) {
  const tone =
    overall === "pass" || overall === "full"  ? "text-biu-sky"
    : overall === "partial"                    ? "text-yellow-300"
    : overall === "fail"                       ? "text-red-300"
    : "muted";
  const glyph =
    overall === "pass" || overall === "full"  ? "✓"
    : overall === "partial"                    ? "~"
    : overall === "fail"                       ? "✗"
    : "?";
  return (
    <span className={`shrink-0 inline-block w-5 text-center font-bold ${tone}`}
          title={overall}>
      {glyph}
    </span>
  );
}


function stepLogLine(ev: AgentEvent): string {
  switch (ev.type) {
    case "session.start":  return `action=${ev.action_id} · scope=${ev.scope_size}`;
    case "runner.step":    return String(ev.message ?? "");
    case "agent.stats":    return Object.entries(ev).filter(([k]) => k !== "type")
                                  .map(([k, v]) => `${k}=${v}`).join(" ");
    case "agent.verdict": {
      const c = (ev.candidate ?? {}) as Record<string, unknown>;
      const v = (ev.verdict ?? {}) as Record<string, unknown>;
      return `${c.name ?? ""} → ${v.overall ?? "?"}`;
    }
    case "session.end":    return `outcome=${ev.outcome} · scope=${ev.scope_size}`;
    case "runner.exit":    return `exit=${ev.return_code}`;
    default:               return JSON.stringify(ev).slice(0, 200);
  }
}
