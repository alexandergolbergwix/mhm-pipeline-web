import {useCallback, useEffect, useState} from "react";

import {ApiError} from "@/api/client";
import {HmoStudioItems, type HmoItemOverridePayload, type HmoStudioItem} from "@/api/hmoStudioItems";
import {HistoryTimeline} from "@/components/history/HistoryTimeline";
import {Glass} from "@/components/glass";
import {HmoItemAiVerdictBadge} from "@/components/hmo/HmoItemAiVerdictBadge";
import {HmoItemMappingBadge} from "@/components/hmo/HmoItemMappingBadge";
import {HmoItemShaclBadge} from "@/components/hmo/HmoItemShaclBadge";
import {HmoItemUploadOutcomeBadge} from "@/components/hmo/HmoItemUploadOutcomeBadge";
import {HmoAuthorityEvidence} from "@/components/hmo/HmoAuthorityEvidence";

const HMO_WIKIBASE_BASE_URL = "https://mhm-hmo.wikibase.cloud";

function AiVerdictReasoningCard({verdict}: {verdict: HmoStudioItem["ai_verdict"]}) {
  if (!verdict) return null;
  const reasoning = verdict.reasoning?.trim();
  const fixes = (verdict as {suggested_fixes?: unknown[]}).suggested_fixes ?? [];
  if (!reasoning && fixes.length === 0) return null;
  return (
    <section className="space-y-2 border-t border-white/5 pt-3" data-testid="hmo-item-ai-verdict-reasoning">
      <h4 className="text-sm font-medium">AI explanation</h4>
      {reasoning && (
        <p className="text-sm text-ink leading-relaxed whitespace-pre-wrap">{reasoning}</p>
      )}
      {fixes.length > 0 && (
        <p className="text-xs muted">{fixes.length} suggested fix(es) — use Apply AI fix above.</p>
      )}
    </section>
  );
}

export interface HmoItemDetailDrawerProps {
  runId: string;
  projectId?: string;
  item: HmoStudioItem;
  allItems: HmoStudioItem[];
  onClose: () => void;
  onSaved: () => void;
  onVerify?: () => void;
  onAutofix?: () => void;
}

export function HmoItemDetailDrawer({
  runId,
  projectId,
  item,
  allItems,
  onClose,
  onSaved,
  onVerify,
  onAutofix,
}: HmoItemDetailDrawerProps) {
  const [pinned, setPinned] = useState(false);
  const [labels, setLabels] = useState({...item.labels});
  const [descriptions, setDescriptions] = useState({...item.descriptions});
  const [stmtPid, setStmtPid] = useState("");
  const [stmtValue, setStmtValue] = useState("");
  const [stmtDatatype, setStmtDatatype] = useState("string");
  const [linkTarget, setLinkTarget] = useState("");
  const [saving, setSaving] = useState(false);
  const [reconciling, setReconciling] = useState(false);
  const [reconcileMsg, setReconcileMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [sparqlLabel, setSparqlLabel] = useState("");
  const [pushing, setPushing] = useState(false);
  const [pushMsg, setPushMsg] = useState<string | null>(null);

  useEffect(() => {
    if (pinned) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, pinned]);

  const save = useCallback(async (payload: HmoItemOverridePayload) => {
    setSaving(true);
    setError(null);
    try {
      await HmoStudioItems.patchOverride(runId, item.local_id, payload);
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setSaving(false);
    }
  }, [item.local_id, onSaved, runId]);

  const handleSave = useCallback(async () => {
    const payload: HmoItemOverridePayload = {
      labels,
      descriptions,
    };
    if (stmtPid.trim()) {
      payload.add_statements = [{
        property_id: stmtPid.trim(),
        datatype: stmtDatatype,
        value: stmtDatatype === "wikibase-item" ? linkTarget || stmtValue : stmtValue,
      }];
    }
    await save(payload);
  }, [descriptions, labels, linkTarget, save, stmtDatatype, stmtPid, stmtValue]);

  const handleReconcile = useCallback(async () => {
    setReconciling(true);
    setReconcileMsg(null);
    try {
      const res = await HmoStudioItems.reconcile(runId, item.local_id);
      setReconcileMsg(`${res.status}: ${res.wikibase_id ?? "—"}`);
      onSaved();
    } catch (e) {
      setReconcileMsg(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setReconciling(false);
    }
  }, [item.local_id, onSaved, runId]);

  const applyAutofix = useCallback(async () => {
    const fixes = (item.ai_verdict as {suggested_fixes?: Array<Record<string, unknown>>} | null)?.suggested_fixes;
    if (!fixes?.length) return;
    setSaving(true);
    try {
      await HmoStudioItems.applyAiFixes(runId, item.local_id, fixes);
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setSaving(false);
    }
  }, [item.ai_verdict, item.local_id, onSaved, runId]);

  const pushToWikibase = useCallback(async () => {
    setPushing(true);
    setPushMsg(null);
    try {
      const res = await HmoStudioItems.pushItem(runId, item.local_id);
      setPushMsg(`${res.status}${res.wikibase_id ? ` \u2192 ${res.wikibase_id}` : ""}${res.message ? `: ${res.message}` : ""}`);
      onSaved();
    } catch (e) {
      setPushMsg(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setPushing(false);
    }
  }, [item.local_id, onSaved, runId]);

  const applyAutofixAndPush = useCallback(async () => {
    const fixes = (item.ai_verdict as {suggested_fixes?: Array<Record<string, unknown>>} | null)?.suggested_fixes;
    if (!fixes?.length) return;
    setSaving(true);
    setPushMsg(null);
    try {
      await HmoStudioItems.applyAiFixes(runId, item.local_id, fixes);
      setSaving(false);
      await pushToWikibase();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
      setSaving(false);
    }
  }, [item.ai_verdict, item.local_id, pushToWikibase, runId]);

  const localTargets = allItems
    .filter((i) => i.local_id !== item.local_id)
    .map((i) => i.local_id);

  return (
    <Glass
      variant="drawer"
      className="fixed right-0 top-0 bottom-0 z-40 w-full max-w-[640px] p-5 overflow-y-auto space-y-4"
      data-testid="hmo-item-detail-drawer"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="kicker">HMO Wikibase item</div>
          <h3 className="text-lg font-medium">{labels.en || labels.he || item.local_id}</h3>
          <p className="muted text-xs font-mono mt-1">
            {item.wikibase_id ? (
              <a
                href={`${HMO_WIKIBASE_BASE_URL}/wiki/Item:${item.wikibase_id}`}
                target="_blank"
                rel="noreferrer"
                className="underline"
              >
                Open project Wikibase entity {item.wikibase_id} ↗
              </a>
            ) : item.source_uri}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs muted flex items-center gap-1">
            <input type="checkbox" checked={pinned} onChange={(e) => setPinned(e.target.checked)} />
            Pin
          </label>
          {item.override_id && projectId && (
            <button type="button" className="button-ghost text-xs" onClick={() => setShowHistory((v) => !v)}>
              📜
            </button>
          )}
          <button type="button" className="button-ghost text-xs" onClick={onClose}>Close</button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <HmoItemMappingBadge status={item.status} />
        <HmoItemShaclBadge issues={item.shacl_issues ?? []} />
        <HmoItemUploadOutcomeBadge
          outcome={item.upload_outcome}
          message={item.upload_message}
          at={item.upload_at}
        />
        <HmoItemAiVerdictBadge verdict={item.ai_verdict} localId={item.local_id} />
        {onVerify && (
          <button type="button" className="button-ghost text-xs" onClick={onVerify} data-testid="hmo-item-verify-btn">
            Verify with AI
          </button>
        )}
        {onAutofix && (
          <button type="button" className="button-ghost text-xs" onClick={onAutofix} data-testid="hmo-item-autofix-btn">
            Autofix with AI
          </button>
        )}
        {(item.ai_verdict as {suggested_fixes?: unknown[]} | null)?.suggested_fixes?.length ? (
          <>
            <button type="button" className="button-primary text-xs" onClick={() => void applyAutofix()} disabled={saving}>
              Apply AI fix
            </button>
            <button type="button" className="button-primary text-xs" onClick={() => void applyAutofixAndPush()} disabled={saving || pushing}>
              Apply fix &amp; push
            </button>
          </>
        ) : null}
        <button type="button" className="button-ghost text-xs" onClick={() => void pushToWikibase()} disabled={pushing}>
          {pushing ? "Pushing…" : "Push to Wikibase now"}
        </button>
      </div>

      {error && <p className="text-danger text-sm">{error}</p>}
      {pushMsg && <p className="text-xs muted">Push result: {pushMsg}</p>}

      <AiVerdictReasoningCard verdict={item.ai_verdict} />

      <HmoAuthorityEvidence claims={item.claims} evidence={item.authority_evidence ?? []} />

      {item.upload_outcome && (
        <p className="text-xs muted">
          Last upload: <b>{item.upload_outcome}</b>
          {item.upload_at ? ` at ${new Date(item.upload_at).toLocaleString()}` : ""}
          {item.upload_message ? ` — ${item.upload_message}` : ""}
        </p>
      )}

      <section className="space-y-2">
        <h4 className="text-sm font-medium">Labels</h4>
        {(["en", "he"] as const).map((lang) => (
          <input
            key={lang}
            value={labels[lang] ?? ""}
            onChange={(e) => setLabels((prev) => ({...prev, [lang]: e.target.value}))}
            placeholder={`Label (${lang})`}
            className="input-glass text-sm w-full"
          />
        ))}
      </section>

      <section className="space-y-2">
        <h4 className="text-sm font-medium">Descriptions</h4>
        {(["en", "he"] as const).map((lang) => (
          <textarea
            key={lang}
            value={descriptions[lang] ?? ""}
            onChange={(e) => setDescriptions((prev) => ({...prev, [lang]: e.target.value}))}
            placeholder={`Description (${lang})`}
            className="input-glass text-sm w-full min-h-[60px]"
          />
        ))}
      </section>

      <section className="space-y-2">
        <h4 className="text-sm font-medium">Add statement</h4>
        <div className="grid grid-cols-2 gap-2">
          <input value={stmtPid} onChange={(e) => setStmtPid(e.target.value)} placeholder="Property PID" className="input-glass text-sm" />
          <select value={stmtDatatype} onChange={(e) => setStmtDatatype(e.target.value)} className="input-glass text-sm">
            <option value="string">string</option>
            <option value="wikibase-item">wikibase-item</option>
            <option value="url">url</option>
            <option value="monolingualtext">monolingualtext</option>
          </select>
        </div>
        {stmtDatatype === "wikibase-item" ? (
          <select value={linkTarget} onChange={(e) => setLinkTarget(e.target.value)} className="input-glass text-sm w-full">
            <option value="">Select local item…</option>
            {localTargets.map((id) => <option key={id} value={id}>{id}</option>)}
          </select>
        ) : (
          <input value={stmtValue} onChange={(e) => setStmtValue(e.target.value)} placeholder="Value" className="input-glass text-sm w-full" />
        )}
      </section>

      <section className="space-y-2 border-t border-white/5 pt-3">
        <h4 className="text-sm font-medium">Reconcile / search Wikibase</h4>
        <button type="button" className="button-ghost text-xs" disabled={reconciling} onClick={() => void handleReconcile()}>
          {reconciling ? "Checking…" : "SPARQL exists-check"}
        </button>
        {reconcileMsg && <p className="text-xs muted">{reconcileMsg}</p>}
        <input
          value={sparqlLabel}
          onChange={(e) => setSparqlLabel(e.target.value)}
          placeholder="Label prefix for near-duplicate search"
          className="input-glass text-sm w-full"
        />
        <p className="text-xs muted">
          Use the Research SPARQL console templates for full Wikibase Cloud queries.
        </p>
      </section>

      <div className="flex gap-2 pt-2">
        <button type="button" className="button-primary text-sm" disabled={saving} onClick={() => void handleSave()}>
          {saving ? "Saving…" : "Save override"}
        </button>
        <button
          type="button"
          className="button-ghost text-sm"
          onClick={() => void save({approved: item.approved === true ? false : true})}
        >
          {item.approved ? "Unapprove" : "Approve"}
        </button>
      </div>

      {showHistory && projectId && item.override_id && (
        <HistoryTimeline
          projectId={projectId}
          entityType="hmo_item_override"
          entityId={item.override_id}
        />
      )}
    </Glass>
  );
}
