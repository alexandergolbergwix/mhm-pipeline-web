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
import { VerdictsTable } from "@/components/VerdictsTable";


export interface AiVerificationModalProps {
  runId:     string;
  scopeKind: ScopeKind;             // "single" | "selection" | "all"
  matchIds?: string[];              // omitted ⇒ all matches in run
  scopeLabel: string;               // for the header ("12 selected" / "all 84 visible" / "1 match")
  onClose:   () => void;
}


export function AiVerificationModal(props: AiVerificationModalProps) {
  const { runId, scopeKind, matchIds, scopeLabel, onClose } = props;

  const [actions,       setActions]       = useState<AgentActionMeta[]>([]);
  const [actionId,      setActionId]      = useState<string>("audit_match");
  // Verdicts are cached on disk by eval-agent — repeated runs over
  // the same scope are nearly free. Tick this to force a fresh
  // Gemini judgement on every candidate (the cache is still
  // refreshed by the new verdicts, so subsequent runs warm-hit).
  const [overrideCache, setOverrideCache] = useState(false);
  const [running,       setRunning]       = useState(false);
  const [events,     setEvents]     = useState<AgentEvent[]>([]);
  const [verdicts,   setVerdicts]   = useState<Record<string, AgentEvent>>({});
  const [flow,       setFlow]       = useState<FlowState>(makeInitialFlowState());
  const [error,      setError]      = useState<string | null>(null);
  // The most recent prior session for this run — drives the "showing
  // results from <date>" banner and lets the curator re-open old
  // verdicts without paying for a fresh Gemini judge run.
  const [lastSession, setLastSession] = useState<
    null | { session_id: string; started_at: string | null; scope_size: number; action_id: string | null }
  >(null);
  const [showingHistorical, setShowingHistorical] = useState(false);

  const cancelRef = useRef<(() => void) | null>(null);

  // Load actions for this scope kind AND auto-prefill verdicts from
  // the most recent prior session if one exists. The curator gets to
  // see the last AI-verified results immediately on modal open — no
  // re-run required.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [list, sessions] = await Promise.all([
          AiVerify.listActions(runId, scopeKind),
          AiVerify.listSessions(runId).catch(() => []),
        ]);
        if (cancelled) return;
        setActions(list);
        if (list.length > 0) setActionId(list[0].id);

        const newest = (sessions ?? []).find(
          (s) => (s.scope_size ?? 0) > 0 && (s.outcome === "complete" || s.outcome === null),
        );
        if (newest) {
          setLastSession({
            session_id: newest.session_id,
            started_at: newest.started_at,
            scope_size: newest.scope_size,
            action_id:  newest.action_id,
          });
          try {
            const full = await AiVerify.session(runId, newest.session_id);
            if (cancelled) return;
            const seeded: Record<string, AgentEvent> = {};
            for (const v of full.verdicts ?? []) {
              const cand = (v.candidate ?? {}) as Record<string, unknown>;
              const matchId = String(cand._match_id ?? cand.record_id ?? "");
              if (matchId) seeded[matchId] = { type: "agent.verdict", ...v };
            }
            if (Object.keys(seeded).length > 0) {
              setVerdicts(seeded);
              setShowingHistorical(true);
            }
          } catch {
            /* the session may be incomplete; ignore */
          }
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();
    return () => { cancelled = true; };
  }, [runId, scopeKind]);

  const lastEvent = events.length > 0 ? events[events.length - 1] : null;

  async function start(): Promise<void> {
    if (running) return;
    setRunning(true); setError(null);
    setEvents([]); setVerdicts({}); setFlow(makeInitialFlowState());
    setShowingHistorical(false);
    const { events: stream, cancel } = streamVerification(runId, {
      action_id:      actionId,
      match_ids:      matchIds,
      override_cache: overrideCache,
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
          <label className="muted text-xs flex items-center gap-2"
                 title="Verdicts are cached on disk. Repeated runs over the same candidates serve from cache for free. Tick to skip the cache and force a fresh Gemini judgement on every candidate.">
            <input type="checkbox" checked={overrideCache}
                   onChange={(e) => setOverrideCache(e.target.checked)}
                   disabled={running} />
            <span>Override cache (force fresh LLM call)</span>
          </label>
          {!running
            ? <button onClick={start} disabled={!action}
                      className="button-primary text-sm">
                {showingHistorical ? "Re-run verification" : "Start verification"}
              </button>
            : <button onClick={stop} className="button-ghost text-sm text-yellow-300">
                Stop
              </button>}
          {error && <span className="text-red-300 text-xs">{error}</span>}
        </div>

        {/* Historical-session banner — visible when we auto-loaded
            verdicts from the most recent prior session. The curator
            can review without paying for a fresh Gemini run. */}
        {showingHistorical && lastSession && !running && (
          <div className="text-xs flex flex-wrap items-center gap-2 px-3 py-2 rounded-lg"
               style={{ background: "rgba(127,196,255,0.08)",
                        border: "1px solid rgba(127,196,255,0.25)" }}>
            <span className="text-ink/90">
              Showing the last verification — {lastSession.started_at
                ? new Date(lastSession.started_at).toLocaleString()
                : "(no timestamp)"} · {lastSession.scope_size} matches
              {lastSession.action_id && ` · ${lastSession.action_id}`}
            </span>
            <button onClick={() => { setVerdicts({}); setShowingHistorical(false); }}
                    className="button-ghost !py-0.5 !px-2 text-[11px] ml-auto">
              Clear
            </button>
          </div>
        )}

        {/* Live diagram */}
        <section className="glass-pill p-3">
          <div className="kicker mb-2">Agent flow</div>
          <AgentFlowDiagram lastEvent={lastEvent} flow={flow} />
        </section>

        {/* Verdicts — full-width research table. Replaces the
            previous tiny truncated list. */}
        <VerdictsTable verdicts={verdicts} />

        {/* Step log — collapsed by default so it doesn't compete
            with the verdict table for attention. */}
        <details className="glass-pill p-3">
          <summary className="kicker cursor-pointer hover:text-ink">
            Step log ({events.length})
          </summary>
          <ul className="space-y-1 text-[11px] font-mono max-h-48 overflow-auto mt-2">
            {events.length === 0 && <li className="muted italic">Waiting for the first event…</li>}
            {events.slice(-80).map((ev, i) => (
              <li key={i} className="flex gap-2">
                <span className="muted shrink-0 w-28">{(ev.type as string).slice(0, 18).padEnd(18)}</span>
                <span className="text-ink/90 truncate">{stepLogLine(ev)}</span>
              </li>
            ))}
          </ul>
        </details>
      </div>
    </div>
  );
}


// ── Helpers ───────────────────────────────────────────────────────────


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
