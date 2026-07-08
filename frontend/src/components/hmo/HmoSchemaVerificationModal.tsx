import {useEffect, useMemo} from "react";

import type {HmoSchemaVerifySession} from "@/hooks/useHmoSchemaVerifySession";
import {
  AgentFlowDiagram,
} from "@/components/AgentFlowDiagram";
import {VerdictsTable} from "@/components/VerdictsTable";
import {Glass} from "@/components/glass";
import {Tier1ModelSelect} from "@/components/Tier1ModelSelect";


export interface HmoSchemaVerificationModalProps {
  runId: string;
  ontologyUris?: string[];
  scopeLabel: string;
  session: HmoSchemaVerifySession;
  onClose: () => void;
}


/**
 * Presentational only — the verification stream itself lives in
 * ``session`` (owned by the panel, not this modal), so closing this
 * modal never cancels an in-flight run. Reopening it renders whatever
 * state the background session has reached.
 */
export function HmoSchemaVerificationModal({
  runId,
  ontologyUris,
  scopeLabel,
  session,
  onClose,
}: HmoSchemaVerificationModalProps) {
  const {
    actions, actionId, setActionId,
    overrideCache, setOverrideCache,
    tierModel, setTierModel, tier1List, tier1Loading,
    running, events, verdicts, flow,
    error, warning, doneMessage,
    start, stop,
  } = session;

  useEffect(() => {
    if (!actionId && actions.length > 0) setActionId(actions[0].id);
  }, [actionId, actions, setActionId]);

  const action = useMemo(
    () => actions.find((a) => a.id === actionId) ?? null,
    [actions, actionId],
  );

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
              Audits every class and property in the latest bootstrap report —
              including rows already mapped on Wikibase — for label, datatype,
              and Wikibase id correctness. Closing this window does not stop a
              run in progress — it keeps judging in the background.
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
          <Tier1ModelSelect
            list={tier1List}
            loading={tier1Loading}
            tierModel={tierModel}
            onChange={setTierModel}
            disabled={running}
          />
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
            <button
              onClick={() => start(runId, ontologyUris)}
              disabled={!action}
              className="button-primary text-sm"
            >
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
