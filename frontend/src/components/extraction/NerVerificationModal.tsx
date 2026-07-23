/**
 * NerVerificationModal — opens from the entity table's
 * "Verify selected with AI" / "Verify all filtered with AI" buttons.
 *
 * Uses the shared verification shell, auto-load-last-session pattern, and
 * override-cache
 * toggle, same {@link AgentFlowDiagram} + {@link VerdictsTable}
 * composition. The differences:
 *
 *  - Calls ``NerVerify.*`` (under ``/extraction/ai-verify/...``)
 *    rather than ``AiVerify.*``.
 *  - Body type field is ``entity_ids`` (not ``match_ids``).
 *  - Verdict join key is ``_entity_id`` (not ``_match_id``).
 *  - Header copy reads "AI verification · NER extraction".
 *  - Fires the optional ``onVerdictsLanded`` callback when a fresh
 *    session ends so the parent table can refresh + show the new
 *    inline pills. (For the historical auto-load case there's no
 *    new data so the callback does NOT fire.)
 */

import {useCallback, useEffect, useMemo, useState} from "react";

import {
  NerVerify,
  type AgentActionMeta,
  type AgentEvent,
  type ScopeKind,
} from "@/api/nerVerify";
import {ExtractionApprovals, type Entity} from "@/api/extractionApprovals";
import {recheckEntity} from "@/api/nerVerify";
import {
  emitEntitiesRefreshed,
  setAiVerdictCache,
} from "@/cache/extractionCache";
import {entityVerdictFingerprint} from "@/cache/verdictKey";
import {verdictStorageKey} from "@/utils/verdictKey";
import {fetchVerifySessionWithJobFallback} from "@/utils/fetchVerifySession";
import {hydrateVerifySession} from "@/utils/verifySessionHydrate";
import {
  AgentFlowDiagram, makeInitialFlowState, type FlowState,
} from "@/components/AgentFlowDiagram";
import { VerdictsTable } from "@/components/VerdictsTable";
import { MarcRecordPopup } from "@/components/MarcRecordPopup";
import {Tier1ModelSelect, useTier1Model} from "@/components/Tier1ModelSelect";
import {Glass} from "@/components/glass";
import {useGlassOverlayLifecycle} from "@/hooks/useGlassOverlayLifecycle";
import {useVerifyJob} from "@/hooks/useVerifyJob";
import {useAuth} from "@/stores/auth";


export interface NerVerificationModalProps {
  runId:      string;
  scopeKind:  ScopeKind;          // "single" | "selection" | "all"
  entityIds?: string[];           // omitted ⇒ all entities in run
  scopeLabel: string;             // for the header ("12 selected" / "all 84 visible" / "1 entity")
  onClose:    () => void;
  /** Fires after a fresh verification run completes successfully and
   *  the backend has persisted verdicts back onto ``ExtractionApproval``
   *  rows. Parent table should refresh entity rows so the new inline
   *  ``AiVerdictPill`` values appear. */
  onVerdictsLanded?: () => void;
}


export function NerVerificationModal(props: NerVerificationModalProps) {
  const {runId, scopeKind, entityIds, scopeLabel, onClose, onVerdictsLanded} = props;

  const userId = useAuth((s) => s.user?.id) ?? "";

  useGlassOverlayLifecycle(true);

  // Control number whose full MARC record is being inspected in the
  // searchable popup. Opened from the control-number column of any
  // verdict row via VerdictsTable's onOpenMarc callback.
  const [marcPopup, setMarcPopup] = useState<string | null>(null);

  const [actions,       setActions]       = useState<AgentActionMeta[]>([]);
  const [actionId,      setActionId]      = useState<string>("audit_ner_extraction");
  // Verdicts are cached on disk by eval-agent — repeated runs over
  // the same scope are nearly free. Tick this to force a fresh
  // Gemini judgement on every candidate (the cache is still
  // refreshed by the new verdicts, so subsequent runs warm-hit).
  const [overrideCache, setOverrideCache] = useState(false);
  const [events,        setEvents]        = useState<AgentEvent[]>([]);
  const [verdicts,      setVerdicts]      = useState<Record<string, AgentEvent>>({});
  const [flow,          setFlow]          = useState<FlowState>(makeInitialFlowState());
  const [error,         setError]         = useState<string | null>(null);
  // The most recent prior session for this run — drives the "showing
  // results from <date>" banner and lets the curator re-open old
  // verdicts without paying for a fresh Gemini judge run.
  const [lastSession,   setLastSession]   = useState<
    null | { session_id: string; started_at: string | null; scope_size: number; action_id: string | null }
  >(null);
  const [showingHistorical, setShowingHistorical] = useState(false);
  const {list: tier1List, tierModel, setTierModel, loading: tier1Loading} = useTier1Model();
  // Bumping this triggers the actions-loading effect to re-fire — used
  // by the Retry button when the initial load failed mid-dev-reload.
  const [reloadKey, setReloadKey] = useState(0);

  const loadSession = useCallback(async (sessionId: string, job?: import("@/api/runJobs").RunJobSnapshot) => {
    const full = await fetchVerifySessionWithJobFallback(
      runId,
      sessionId,
      "ner_verify",
      NerVerify.session,
      job,
    );
    const hydrated = hydrateVerifySession(full, (row) => {
      const cand = (row.candidate ?? {}) as Record<string, unknown>;
      const entityId = String(cand._entity_id ?? cand.record_id ?? "");
      if (entityId) return entityId;
      return verdictStorageKey({type: "agent.verdict", ...row});
    });
    for (const ev of Object.values(hydrated.verdicts)) {
      const cand = (ev.candidate ?? {}) as Record<string, unknown>;
      void (async () => {
        try {
          const fp = await entityVerdictFingerprint({
            control_number: String(cand.control_number ?? ""),
            source:         String(cand.source ?? "person_ner") as Entity["source"],
            start:          Number(cand.start ?? 0),
            end:            Number(cand.end ?? 0),
            text:           String(cand.text ?? ""),
            type:           String(cand.type ?? "OTHER") as Entity["type"],
            role:           String(cand.role ?? "") as Entity["role"],
            ai_verdict:     {overall: "unknown", model: String(ev.judge_id ?? ev.model ?? "")},
          });
          setAiVerdictCache(userId, runId, fp, ev);
        } catch {
          // non-fatal
        }
      })();
    }
    setEvents(hydrated.events);
    setVerdicts(hydrated.verdicts);
    setFlow(hydrated.flow);
  }, [runId, userId]);

  const handleVerifyFailed = useCallback((msg: string) => setError(msg), []);
  const handleVerifyComplete = useCallback(() => {
    emitEntitiesRefreshed(userId, runId);
    onVerdictsLanded?.();
  }, [userId, runId, onVerdictsLanded]);

  const {running, start: startVerifyJob, stop} = useVerifyJob({
    runId,
    kind: "ner_verify",
    loadSession,
    onFailed: handleVerifyFailed,
    onComplete: handleVerifyComplete,
  });

  // Load actions for this scope kind AND auto-prefill verdicts from
  // the most recent prior session if one exists. The curator gets to
  // see the last AI-verified results immediately on modal open — no
  // re-run required. If the actions request fails (e.g. dev server was
  // mid-reload), we auto-retry once after 1.5 s before surfacing the
  // error to the user.
  useEffect(() => {
    let cancelled = false;
    let attempt = 0;
    async function loadOnce(): Promise<boolean> {
      try {
        const [list, sessions] = await Promise.all([
          NerVerify.listActions(runId, scopeKind),
          NerVerify.listSessions(runId).catch(() => []),
        ]);
        if (cancelled) return true;
        setActions(list);
        if (list.length > 0) setActionId(list[0].id);
        setError(null);

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
            const full = await fetchVerifySessionWithJobFallback(
              runId,
              newest.session_id,
              "ner_verify",
              NerVerify.session,
            );
            if (cancelled) return true;
            const hydrated = hydrateVerifySession(full, (row) => {
              const cand = (row.candidate ?? {}) as Record<string, unknown>;
              const entityId = String(cand._entity_id ?? cand.record_id ?? "");
              if (entityId) return entityId;
              return verdictStorageKey({type: "agent.verdict", ...row});
            });
            if (Object.keys(hydrated.verdicts).length > 0) {
              setVerdicts(hydrated.verdicts);
              setShowingHistorical(true);
            }
          } catch {
            /* the session may be incomplete; ignore */
          }
        }
        return true;
      } catch (e) {
        if (cancelled) return true;
        attempt += 1;
        if (attempt < 2) return false;
        setError((e as Error).message);
        return true;
      }
    }
    (async () => {
      while (!(await loadOnce())) {
        if (cancelled) return;
        await new Promise((r) => setTimeout(r, 1500));
      }
    })();
    return () => { cancelled = true; };
  }, [runId, scopeKind, reloadKey]);

  const lastEvent = events.length > 0 ? events[events.length - 1] : null;

  async function start(): Promise<void> {
    if (running) return;
    setError(null);
    setEvents([]);
    setVerdicts({});
    setFlow(makeInitialFlowState());
    setShowingHistorical(false);
    try {
      await startVerifyJob({
        action_id:      actionId,
        entity_ids:     entityIds,
        override_cache: overrideCache,
        tier_model:     tierModel,
      });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const action = useMemo(
    () => actions.find((a) => a.id === actionId) ?? null,
    [actions, actionId],
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-stretch justify-center bg-black/60 backdrop-blur-md p-4 md:p-6"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <Glass variant="modal" refraction={false} className="max-w-7xl w-full max-h-full overflow-auto p-6 space-y-4">
        {/* Header */}
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="kicker">AI verification · NER extraction · {scopeLabel}</div>
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
            <select
              value={actionId}
              onChange={(e) => setActionId(e.target.value)}
              disabled={running || actions.length === 0}
              className="input-glass !py-1 text-sm min-w-[260px]"
            >
              {actions.length === 0 && <option>Loading…</option>}
              {actions.map((a) => (
                <option key={a.id} value={a.id}>{a.label}</option>
              ))}
            </select>
          </div>
          <Tier1ModelSelect
            list={tier1List}
            loading={tier1Loading}
            tierModel={tierModel}
            onChange={setTierModel}
            disabled={running}
          />
          <label
            className="muted text-xs flex items-center gap-2"
            title="Verdicts are cached in the shared team store (Redis/Postgres). Repeated runs over the same inputs are nearly free. Tick to skip the cache and force a fresh Gemini judgement on every candidate."
          >
            <input
              type="checkbox"
              checked={overrideCache}
              onChange={(e) => setOverrideCache(e.target.checked)}
              disabled={running}
            />
            <span>Override cache (force fresh LLM call)</span>
          </label>
          {!running
            ? <button onClick={start} disabled={!action}
                      className="button-primary text-sm">
                {showingHistorical ? "Re-run verification" : "Start verification"}
              </button>
            : <button onClick={stop} className="button-ghost text-sm text-warn">
                Stop
              </button>}
          {error && (
            <span className="text-danger text-xs flex items-center gap-2">
              {error}
              <button type="button"
                      onClick={() => { setError(null); setReloadKey((k) => k + 1); }}
                      className="button-ghost !py-0.5 !px-2 text-[11px]">
                Retry
              </button>
            </span>
          )}
        </div>

        {/* Historical-session banner — visible when we auto-loaded
            verdicts from the most recent prior session. The curator
            can review without paying for a fresh Gemini run. */}
        {showingHistorical && lastSession && !running && (
          <div
            className="text-xs flex flex-wrap items-center gap-2 px-3 py-2 rounded-lg"
            style={{
              background: "rgba(127,196,255,0.08)",
              border:     "1px solid rgba(127,196,255,0.25)",
            }}
          >
            <span className="text-ink/90">
              Showing the last verification — {lastSession.started_at
                ? new Date(lastSession.started_at).toLocaleString()
                : "(no timestamp)"} · {lastSession.scope_size} entities
              {lastSession.action_id && ` · ${lastSession.action_id}`}
            </span>
            <button
              onClick={() => { setVerdicts({}); setShowingHistorical(false); }}
              className="button-ghost !py-0.5 !px-2 text-[11px] ml-auto"
            >
              Clear
            </button>
          </div>
        )}

        {/* Live diagram */}
        <Glass as="section" variant="compact" refraction={false} className="p-3">
          <div className="kicker mb-2">Agent flow</div>
          <AgentFlowDiagram lastEvent={lastEvent} flow={flow} />
        </Glass>

        {/* Verdicts — full-width research table. Each control-number
            cell opens a searchable popup with the full MARC record for
            that manuscript. */}
        <VerdictsTable
          verdicts={verdicts}
          onOpenMarc={(controlNumber) => setMarcPopup(controlNumber)}
          onApplyFix={async (entityId, target, value) => {
            const patch = target ? {[target]: value} : {text: value};
            await ExtractionApprovals.patch(runId, entityId, patch);
            emitEntitiesRefreshed(userId, runId);
            try {
              await recheckEntity(runId, entityId);
            } catch (err) {
              console.warn("NER modal auto-fix re-verify failed", err);
            }
          }}
        />

        {marcPopup && (
          <MarcRecordPopup
            runId={runId}
            controlNumber={marcPopup}
            onClose={() => setMarcPopup(null)}
          />
        )}

        {/* Step log — collapsed by default. */}
        <Glass as="details" variant="compact" refraction={false} className="p-3">
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
        </Glass>
      </Glass>
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
      return `${c.name ?? c.text ?? ""} → ${v.overall ?? "?"}`;
    }
    case "session.end":    return `outcome=${ev.outcome} · scope=${ev.scope_size}`;
    case "runner.exit":    return `exit=${ev.return_code}`;
    default:               return JSON.stringify(ev).slice(0, 200);
  }
}
