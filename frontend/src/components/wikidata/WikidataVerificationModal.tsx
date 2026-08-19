/**
 * WikidataVerificationModal — eval-agent verification for Wikidata
 * Studio items. It mirrors the NER verification flow but sends
 * `item_ids` and keys verdicts by item `local_id`.
 */

import {useCallback, useEffect, useMemo, useState} from "react";

import {applyWikidataFixes, type WikidataSuggestedFix} from "@/utils/wikidataAutofix";
import {fetchVerifySessionWithJobFallback} from "@/utils/fetchVerifySession";
import {
  hydrateVerifySession,
  mergeFlowWithJobProgress,
} from "@/utils/verifySessionHydrate";
import {jobFingerprint} from "@/utils/renderStable";
import {selectActiveJob, useRunJobs} from "@/stores/runJobs";
import {
  WikidataVerify,
  type AgentActionMeta,
  type AgentEvent,
  type ScopeKind,
} from "@/api/wikidataVerify";
import {
  AgentFlowDiagram,
  makeInitialFlowState,
  type FlowState,
} from "@/components/AgentFlowDiagram";
import { VerdictsTable } from "@/components/VerdictsTable";
import {Tier1ModelSelect, useTier1Model} from "@/components/Tier1ModelSelect";
import {Glass} from "@/components/glass";
import {useVerifyJob} from "@/hooks/useVerifyJob";
import {continueVerifyLabel} from "@/utils/verifyResume";


export interface WikidataVerificationModalProps {
  runId: string;
  scopeKind: ScopeKind;
  itemIds?: string[];
  scopeLabel: string;
  initialActionId?: string;
  /** Must match the Studio projection toggle that produced ``itemIds``. */
  source?: "legacy" | "canonical";
  approvedOnly?: boolean;
  onClose: () => void;
  onVerdictsLanded?: () => void;
  /**
   * Live verdicts as the judge streams them, so the review table can patch the
   * rows it already shows instead of sitting on `— —` until the run ends
   * (Rule W-110: patch rows, never flicker the table).
   */
  onVerdictStream?: (overallByItemId: Record<string, string>) => void;
}


export function WikidataVerificationModal(props: WikidataVerificationModalProps) {
  const {
    runId, scopeKind, itemIds, scopeLabel, initialActionId, onClose, onVerdictsLanded,
    onVerdictStream,
    source = "canonical",
    approvedOnly = false,
  } = props;

  const [actions, setActions] = useState<AgentActionMeta[]>([]);
  const [actionId, setActionId] = useState("audit_wikidata_item");
  const [overrideCache, setOverrideCache] = useState(false);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [verdicts, setVerdicts] = useState<Record<string, AgentEvent>>({});
  const [flow, setFlow] = useState<FlowState>(makeInitialFlowState());
  const [error, setError] = useState<string | null>(null);
  const [lastSession, setLastSession] = useState<
    null | { session_id: string; started_at: string | null; scope_size: number; action_id: string | null }
  >(null);
  const [showingHistorical, setShowingHistorical] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const {list: tier1List, tierModel, setTierModel, loading: tier1Loading} = useTier1Model();

  const loadSession = useCallback(async (sessionId: string, job?: import("@/api/runJobs").RunJobSnapshot) => {
    const full = await fetchVerifySessionWithJobFallback(
      runId,
      sessionId,
      "wikidata_verify",
      WikidataVerify.session,
      job,
    );
    const hydrated = hydrateVerifySession(full, itemIdFromVerdict);
    setEvents(hydrated.events);
    setVerdicts(hydrated.verdicts);
    setFlow(hydrated.flow);
  }, [runId]);

  const handleVerifyFailed = useCallback((msg: string) => setError(msg), []);
  const handleVerifyComplete = useCallback(() => onVerdictsLanded?.(), [onVerdictsLanded]);

  const jobsRecord = useRunJobs((s) => s.jobs);
  const verifyJob = useMemo(
    () => selectActiveJob(jobsRecord, runId, "wikidata_verify"),
    [jobsRecord, runId],
  );
  const verifyJobKey = verifyJob ? jobFingerprint(verifyJob) : null;

  const {running, start: startVerifyJob, continueFromPause, stop, progress, resumeOffer} = useVerifyJob({
    runId,
    kind: "wikidata_verify",
    loadSession,
    onFailed: handleVerifyFailed,
    onComplete: handleVerifyComplete,
  });

  useEffect(() => {
    if (!running || !progress) return;
    setFlow((prev) => mergeFlowWithJobProgress(prev, progress));
  }, [running, progress, verifyJobKey]);

  useEffect(() => {
    let cancelled = false;
    let attempt = 0;
    async function loadOnce(): Promise<boolean> {
      try {
        const [list, sessions] = await Promise.all([
          WikidataVerify.listActions(runId, scopeKind),
          WikidataVerify.listSessions(runId).catch(() => []),
        ]);
        if (cancelled) return true;
        setActions(list);
        if (list.length > 0) {
          const preferred = initialActionId && list.some((a) => a.id === initialActionId)
            ? initialActionId
            : list[0].id;
          setActionId(preferred);
        }
        setError(null);

        const newest = (sessions ?? []).find(
          (s) => (s.scope_size ?? 0) > 0 && (s.outcome === "complete" || s.outcome === null),
        );
        if (newest) {
          setLastSession({
            session_id: newest.session_id,
            started_at: newest.started_at,
            scope_size: newest.scope_size,
            action_id: newest.action_id,
          });
          try {
            const full = await WikidataVerify.session(runId, newest.session_id);
            if (cancelled) return true;
            const seeded: Record<string, AgentEvent> = {};
            for (const v of full.verdicts ?? []) {
              const itemId = itemIdFromVerdict(v);
              if (itemId) seeded[itemId] = { type: "agent.verdict", ...v };
            }
            if (Object.keys(seeded).length > 0) {
              setVerdicts(seeded);
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
        setError(e instanceof Error ? e.message : String(e));
        return true;
      }
    }
    (async () => {
      while (!(await loadOnce())) {
        if (cancelled) return;
        await new Promise((resolve) => window.setTimeout(resolve, 1500));
      }
    })();
    return () => { cancelled = true; };
  }, [runId, scopeKind, reloadKey, initialActionId]);

  // Publish each judged item upward as it lands. Only the `overall` string is
  // passed: the table renders nothing else, and sending the full event would
  // re-render 313 rows on every verdict.
  useEffect(() => {
    if (!onVerdictStream) return;
    const overallByItemId: Record<string, string> = {};
    for (const [itemId, event] of Object.entries(verdicts)) {
      const verdict = recordValue((event as Record<string, unknown>).verdict);
      const overall = stringValue(verdict?.overall) || stringValue(
        (event as Record<string, unknown>).overall,
      );
      if (itemId && overall) overallByItemId[itemId] = overall;
    }
    if (Object.keys(overallByItemId).length > 0) onVerdictStream(overallByItemId);
  }, [verdicts, onVerdictStream]);

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
        action_id: actionId,
        item_ids: itemIds,
        approved_only: approvedOnly,
        source,
        override_cache: overrideCache,
        tier_model: tierModel,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function continueVerification(): Promise<void> {
    if (running) return;
    setError(null);
    setOverrideCache(false);
    setShowingHistorical(false);
    try {
      await continueFromPause({
        action_id: actionId,
        item_ids: itemIds,
        approved_only: approvedOnly,
        source,
        tier_model: tierModel,
        override_cache: false,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const action = useMemo(
    () => actions.find((a) => a.id === actionId) ?? null,
    [actions, actionId],
  );

  async function handleApplyFixes(localId: string, fixes: WikidataSuggestedFix[]): Promise<void> {
    await applyWikidataFixes(runId, localId, fixes);
    setVerdicts((prev) => {
      const updated: Record<string, AgentEvent> = {};
      for (const [k, v] of Object.entries(prev)) {
        const cand = (v.candidate ?? {}) as Record<string, unknown>;
        const isTarget =
          String(cand._item_id ?? cand._local_id ?? cand.local_id ?? v.record_id ?? "") === localId
          || k === localId;
        updated[k] = isTarget ? {...v, _fix_applied: true} : v;
      }
      return updated;
    });
    onVerdictsLanded?.();
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-stretch justify-center bg-black/60 backdrop-blur-md p-4 md:p-6"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <Glass variant="modal" className="max-w-7xl w-full max-h-full overflow-auto p-6 space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="kicker">AI verification · Wikidata Studio · {scopeLabel}</div>
            <h3 className="text-xl font-semibold">Verify with AI agent</h3>
            {action && <p className="muted text-sm max-w-2xl">{action.description}</p>}
          </div>
          <button onClick={onClose} className="button-ghost !py-1 !px-3 text-sm" title="Close">
            X
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
              {actions.length === 0 && <option>Loading...</option>}
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
            title="Verdicts are cached on disk. Repeated runs over the same items serve from cache for free. Tick to skip the cache and force a fresh LLM judgement on every item."
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
            ? (
              <>
                {resumeOffer && (
                  <button
                    type="button"
                    onClick={() => { void continueVerification(); }}
                    disabled={!action}
                    className="button-primary text-sm"
                    data-testid="wikidata-verify-continue"
                  >
                    {continueVerifyLabel(resumeOffer)}
                  </button>
                )}
                <button
                  onClick={() => { void start(); }}
                  disabled={!action}
                  className={resumeOffer ? "button-ghost text-sm" : "button-primary text-sm"}
                >
                  {showingHistorical ? "Re-run verification" : "Start verification"}
                </button>
              </>
            )
            : (
              <button onClick={stop} className="button-ghost text-sm text-warn">
                Stop
              </button>
            )}
          {error && (
            <span className="text-danger text-xs flex items-center gap-2">
              {error}
              <button
                type="button"
                onClick={() => { setError(null); setReloadKey((k) => k + 1); }}
                className="button-ghost !py-0.5 !px-2 text-[11px]"
              >
                Retry
              </button>
            </span>
          )}
        </div>

        {showingHistorical && lastSession && !running && (
          <div
            className="text-xs flex flex-wrap items-center gap-2 px-3 py-2 rounded-lg"
            style={{
              background: "rgba(127,196,255,0.08)",
              border: "1px solid rgba(127,196,255,0.25)",
            }}
          >
            <span className="text-ink/90">
              Showing the last verification - {lastSession.started_at
                ? new Date(lastSession.started_at).toLocaleString()
                : "(no timestamp)"} · {lastSession.scope_size} items
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

        <Glass as="section" variant="compact" className="p-3">
          <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
            <div className="kicker">Agent flow</div>
            {running && (
              <span className="text-xs muted" data-testid="wikidata-verify-cache-stats">
                {Number(progress?.total ?? flow.total ?? 0) > 0 && (
                  <>
                    {Number(progress?.processed ?? flow.judged ?? 0)} / {Number(progress?.total ?? flow.total ?? 0)} judged
                  </>
                )}
                {(flow.cacheHits > 0 || Number(progress?.cache_hits ?? 0) > 0) && (
                  <>
                    {(Number(progress?.total ?? flow.total ?? 0) > 0) ? " · " : ""}
                    {Math.max(flow.cacheHits, Number(progress?.cache_hits ?? 0))} from cache (no LLM)
                  </>
                )}
              </span>
            )}
          </div>
          <AgentFlowDiagram lastEvent={lastEvent} flow={flow} variant="wikidata" />
          {!overrideCache && !running && (
            <p className="text-xs muted mt-2">
              Identical judge prompts reuse the verdict cache (Postgres + on-disk). Tick
              &quot;Override cache&quot; only when you need a fresh LLM call.
            </p>
          )}
          {running
            && Number(progress?.processed ?? 0) > 0
            && Object.keys(verdicts).length === 0 && (
            <p className="text-xs muted mt-2">
              Live verdict rows fill when the job finishes (or from a slim
              snapshot) — progress above is still updating.
            </p>
          )}
        </Glass>

        <VerdictsTable
          verdicts={verdicts}
          onApplyFixes={(localId, fixes) => handleApplyFixes(localId, fixes)}
        />

        <Glass as="details" variant="compact" className="p-3">
          <summary className="kicker cursor-pointer hover:text-ink">
            Step log ({events.length})
          </summary>
          <ul className="space-y-1 text-[11px] font-mono max-h-48 overflow-auto mt-2">
            {events.length === 0 && <li className="muted italic">Waiting for the first event...</li>}
            {events.slice(-80).map((ev, i) => (
              <li key={i} className="flex gap-2">
                <span className="muted shrink-0 w-28">{ev.type.slice(0, 18).padEnd(18)}</span>
                <span className="text-ink/90 truncate">{stepLogLine(ev)}</span>
              </li>
            ))}
          </ul>
        </Glass>
      </Glass>
    </div>
  );
}


function itemIdFromVerdict(ev: Record<string, unknown>): string {
  const candidate = recordValue(ev.candidate);
  return stringValue(candidate?._item_id)
      || stringValue(candidate?._local_id)
      || stringValue(candidate?.local_id)
      || stringValue(candidate?.id)
      || stringValue(ev._item_id)
      || stringValue(ev._local_id)
      || stringValue(ev.local_id)
      || stringValue(ev.item_id)
      || "";
}


function stepLogLine(ev: AgentEvent): string {
  switch (ev.type) {
    case "session.start":
      return `action=${stringValue(ev.action_id)} · scope=${stringValue(ev.scope_size)}`;
    case "runner.step":
      return stringValue(ev.message);
    case "agent.stats":
      return Object.entries(ev).filter(([k]) => k !== "type")
        .map(([k, v]) => `${k}=${String(v)}`).join(" ");
    case "agent.verdict": {
      const candidate = recordValue(ev.candidate);
      const verdict = recordValue(ev.verdict);
      const label = stringValue(candidate?.label)
        || stringValue(candidate?.name)
        || stringValue(candidate?.text)
        || itemIdFromVerdict(ev);
      return `${label} -> ${stringValue(verdict?.overall) || "?"}`;
    }
    case "session.end":
      return `outcome=${stringValue(ev.outcome)} · scope=${stringValue(ev.scope_size)}`;
    case "runner.exit":
      return `exit=${stringValue(ev.return_code)}`;
    default:
      return JSON.stringify(ev).slice(0, 200);
  }
}


function recordValue(value: unknown): Record<string, unknown> | null {
  return isRecord(value) ? value : null;
}


function stringValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}


function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
