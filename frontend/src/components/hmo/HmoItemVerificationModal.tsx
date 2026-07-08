import {useCallback, useEffect, useState} from "react";

import {AgentFlowDiagram, type FlowState} from "@/components/AgentFlowDiagram";
import {VerdictsTable} from "@/components/VerdictsTable";
import {Glass} from "@/components/glass";
import type {HmoItemVerifySession} from "@/hooks/useHmoItemVerifySession";

export interface HmoItemVerificationModalProps {
  runId: string;
  scopeLabel: string;
  itemIds?: string[];
  initialActionId?: string;
  session: HmoItemVerifySession;
  onClose: () => void;
}

export function HmoItemVerificationModal({
  runId,
  scopeLabel,
  itemIds,
  initialActionId,
  session,
  onClose,
}: HmoItemVerificationModalProps) {
  const {
    actions, actionId, setActionId, overrideCache, setOverrideCache,
    running, events, verdicts, flow, error, warning, doneMessage, start, stop,
  } = session;

  const [localFlow, setLocalFlow] = useState<FlowState>(flow);

  useEffect(() => {
    setLocalFlow(flow);
  }, [flow]);

  useEffect(() => {
    if (initialActionId) {
      setActionId(initialActionId);
    }
  }, [initialActionId, setActionId]);

  const handleStart = useCallback(() => {
    start(runId, itemIds);
  }, [itemIds, runId, start]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
      <Glass variant="modal" className="w-full max-w-4xl max-h-[90vh] overflow-y-auto p-5 space-y-4">
        <div className="flex justify-between items-baseline">
          <div>
            <div className="kicker">AI verification</div>
            <h3 className="text-lg font-medium">{scopeLabel}</h3>
          </div>
          <button type="button" className="button-ghost text-sm" onClick={onClose}>Close</button>
        </div>

        <div className="flex flex-wrap gap-3 items-center text-sm">
          <select value={actionId} onChange={(e) => setActionId(e.target.value)} className="input-glass text-sm" data-testid="hmo-item-verify-action">
            {actions.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}
          </select>
          <label className="flex items-center gap-1 muted">
            <input type="checkbox" checked={overrideCache} onChange={(e) => setOverrideCache(e.target.checked)} />
            Override cache
          </label>
          {!running ? (
            <button type="button" className="button-primary text-sm" onClick={handleStart}>Start</button>
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
        {warning && <p className="text-warn text-sm">{warning}</p>}
        {doneMessage && <p className="text-biu-sky text-sm">{doneMessage}</p>}

        <AgentFlowDiagram lastEvent={events[events.length - 1] ?? null} flow={localFlow} />
        <VerdictsTable verdicts={verdicts} />
      </Glass>
    </div>
  );
}
