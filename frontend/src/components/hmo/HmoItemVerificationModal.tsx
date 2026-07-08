import {useCallback, useEffect, useMemo, useState} from "react";

import {HmoItemVerify} from "@/api/hmoItemVerify";
import type {AgentActionMeta, AgentEvent} from "@/api/wikidataVerify";
import {
  AgentFlowDiagram,
  makeInitialFlowState,
  type FlowState,
} from "@/components/AgentFlowDiagram";
import {VerdictsTable} from "@/components/VerdictsTable";
import {Tier1ModelSelect, useTier1Model} from "@/components/Tier1ModelSelect";
import {Glass} from "@/components/glass";
import {useGlassOverlayLifecycle} from "@/hooks/useGlassOverlayLifecycle";
import {useVerifyJob} from "@/hooks/useVerifyJob";
import {fetchVerifySessionWithJobFallback} from "@/utils/fetchVerifySession";
import {hydrateVerifySession, mergeFlowWithJobProgress} from "@/utils/verifySessionHydrate";

function verdictLocalId(row: Record<string, unknown>): string {
  const cand = (row.candidate ?? {}) as Record<string, unknown>;
  return String(cand._local_id ?? cand.local_id ?? "");
}

export interface HmoItemVerificationModalProps {
  runId: string;
  scopeLabel: string;
  itemIds?: string[];
  initialActionId?: string;
  onClose: () => void;
  onVerdictsLanded?: () => void;
}

export function HmoItemVerificationModal({
  runId,
  scopeLabel,
  itemIds,
  initialActionId,
  onClose,
  onVerdictsLanded,
}: HmoItemVerificationModalProps) {
  useGlassOverlayLifecycle(true);

  const [actions, setActions] = useState<AgentActionMeta[]>([]);
  const [actionId, setActionId] = useState("audit_hmo_wikibase_item");
  const [overrideCache, setOverrideCache] = useState(false);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [verdicts, setVerdicts] = useState<Record<string, AgentEvent>>({});
  const [flow, setFlow] = useState<FlowState>(makeInitialFlowState());
  const [error, setError] = useState<string | null>(null);
  const [doneMessage, setDoneMessage] = useState<string | null>(null);
  const {list: tier1List, tierModel, setTierModel, loading: tier1Loading} = useTier1Model();

  const loadSession = useCallback(async (sessionId: string, job?: import("@/api/runJobs").RunJobSnapshot) => {
    const full = await fetchVerifySessionWithJobFallback(
      runId,
      sessionId,
      "hmo_item_verify",
      HmoItemVerify.session,
      job,
    );
    const hydrated = hydrateVerifySession(full, verdictLocalId);
    setEvents(hydrated.events);
    setVerdicts(hydrated.verdicts);
    setFlow(mergeFlowWithJobProgress(hydrated.flow, job?.progress));
  }, [runId]);

  const handleVerifyFailed = useCallback((msg: string) => {
    setError(msg);
    setDoneMessage(null);
  }, []);

  const handleVerifyComplete = useCallback(() => {
    setDoneMessage("Verification complete.");
    onVerdictsLanded?.();
  }, [onVerdictsLanded]);

  const {running, start: startVerifyJob, stop, progress} = useVerifyJob({
    runId,
    kind: "hmo_item_verify",
    loadSession,
    onFailed: handleVerifyFailed,
    onComplete: handleVerifyComplete,
  });

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    void HmoItemVerify.actions(runId, "selection")
      .then((list) => {
        if (cancelled) return;
        setActions(list);
        if (initialActionId) {
          setActionId(initialActionId);
        } else if (list.length > 0) {
          setActionId(list[0].id);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => { cancelled = true; };
  }, [runId, initialActionId]);

  useEffect(() => {
    if (!progress) return;
    setFlow((prev) => mergeFlowWithJobProgress(prev, progress));
  }, [progress]);

  const handleStart = useCallback(async () => {
    if (running) return;
    setError(null);
    setDoneMessage(null);
    setEvents([]);
    setVerdicts({});
    setFlow(makeInitialFlowState());
    try {
      await startVerifyJob({
        action_id: actionId,
        item_ids: itemIds,
        override_cache: overrideCache,
        tier_model: tierModel,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [actionId, itemIds, overrideCache, running, startVerifyJob, tierModel]);

  const lastEvent = events.length > 0 ? events[events.length - 1] : null;
  const action = useMemo(
    () => actions.find((a) => a.id === actionId) ?? null,
    [actions, actionId],
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
      <Glass variant="modal" className="w-full max-w-4xl max-h-[90vh] overflow-y-auto p-5 space-y-4">
        <div className="flex justify-between items-baseline">
          <div>
            <div className="kicker">AI verification</div>
            <h3 className="text-lg font-medium">{scopeLabel}</h3>
            {action && <p className="muted text-sm mt-1">{action.description}</p>}
          </div>
          <button type="button" className="button-ghost text-sm" onClick={onClose}>Close</button>
        </div>

        <div className="flex flex-wrap gap-3 items-center text-sm">
          <select value={actionId} onChange={(e) => setActionId(e.target.value)} className="input-glass text-sm" data-testid="hmo-item-verify-action">
            {actions.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}
          </select>
          <Tier1ModelSelect
            list={tier1List}
            loading={tier1Loading}
            tierModel={tierModel}
            onChange={setTierModel}
            disabled={running}
          />
          <label className="flex items-center gap-1 muted">
            <input type="checkbox" checked={overrideCache} onChange={(e) => setOverrideCache(e.target.checked)} />
            Override cache
          </label>
          {!running ? (
            <button type="button" className="button-primary text-sm" onClick={() => void handleStart()}>Start</button>
          ) : (
            <button type="button" className="button-ghost text-sm" onClick={stop}>Stop</button>
          )}
        </div>

        {actionId === "autofix_hmo_wikibase_item" && (
          <p className="text-xs muted">
            Compares each item&apos;s live Wikibase entity against the build and proposes
            high-confidence fixes. Open a row afterward to use <b>Apply AI fix</b> or
            <b> Apply fix &amp; push</b>.
          </p>
        )}

        {error && <p className="text-danger text-sm">{error}</p>}
        {doneMessage && <p className="text-biu-sky text-sm">{doneMessage}</p>}

        <AgentFlowDiagram lastEvent={lastEvent} flow={flow} />
        <VerdictsTable verdicts={verdicts} />
      </Glass>
    </div>
  );
}
