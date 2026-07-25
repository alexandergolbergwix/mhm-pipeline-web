import {useCallback, useEffect, useState} from "react";

import {ApiError} from "@/api/client";
import {
  Studio,
  type ItemOverridePayload,
  type StudioItem,
} from "@/api/wikidataStudio";
import {HistoryTimeline} from "@/components/history/HistoryTimeline";
import {Glass, GlassPill} from "@/components/glass";
import {UploadOutcomeBadge} from "@/components/shared/UploadOutcomeBadge";
import {collectIds, PropertyPill, ValueRendering} from "@/components/StatementCells";
import {ItemValidatorBadge} from "@/components/wikidata/ItemValidatorBadge";
import {WikidataComparePanel} from "@/components/wikidata/WikidataComparePanel";
import {WikidataItemAiVerdictBadge} from "@/components/wikidata/WikidataItemAiVerdictBadge";
import {WikidataItemDataStatusBadge} from "@/components/wikidata/WikidataItemDataStatusBadge";
import {useLabelStore} from "@/api/wikidataLabels";

function AiVerdictReasoningCard({verdict}: {verdict: StudioItem["ai_verdict"]}) {
  if (!verdict) return null;
  const reasoning = verdict.reasoning?.trim();
  const fixes = (verdict as {suggested_fixes?: unknown[]}).suggested_fixes ?? [];
  if (!reasoning && fixes.length === 0) return null;
  return (
    <section className="space-y-2 border-t border-white/5 pt-3" data-testid="wikidata-item-ai-verdict-reasoning">
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

export interface WikidataItemDetailDrawerProps {
  runId: string;
  projectId?: string;
  item: StudioItem;
  approvedOnly: boolean;
  moratoriumLifted?: boolean;
  testMode?: boolean;
  uploadTarget?: "test" | "live";
  onClose: () => void;
  onSaved: () => void;
  onVerify?: () => void;
  onAutofix?: () => void;
}

function labelOf(item: StudioItem): string {
  const l = item.labels ?? {};
  return l.en || l.he || Object.values(l)[0] || item.local_id || "";
}

export function WikidataItemDetailDrawer({
  runId,
  projectId,
  item,
  approvedOnly,
  moratoriumLifted = false,
  testMode = false,
  uploadTarget = "test",
  onClose,
  onSaved,
  onVerify,
  onAutofix,
}: WikidataItemDetailDrawerProps) {
  const [pinned, setPinned] = useState(false);
  const [labels, setLabels] = useState({...(item.labels ?? {})});
  const [descriptions, setDescriptions] = useState({...(item.descriptions ?? {})});
  const [aliasesHe, setAliasesHe] = useState((item.aliases?.he ?? []).join(" · "));
  const [excluded, setExcluded] = useState<Set<number>>(new Set());
  const [excludeSaving, setExcludeSaving] = useState(false);
  const [saving, setSaving] = useState(false);
  const [reconciling, setReconciling] = useState(false);
  const [reconcileMsg, setReconcileMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [compareOpen, setCompareOpen] = useState(false);
  const [pushing, setPushing] = useState(false);
  const [pushMsg, setPushMsg] = useState<string | null>(null);
  const [acceptForeign, setAcceptForeign] = useState(Boolean(item.accept_foreign_modify));
  const labelStore = useLabelStore();

  const statements = item.statements ?? [];
  const wikidataQid = item.existing_qid || null;
  const historyId = item.local_id ?? "";

  useEffect(() => {
    setAcceptForeign(Boolean(item.accept_foreign_modify));
  }, [item.accept_foreign_modify, item.local_id, item.existing_qid]);

  useEffect(() => {
    if (pinned) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, pinned]);

  useEffect(() => {
    const ids: string[] = [];
    statements.forEach((s) => collectIds(s, ids));
    labelStore.resolve(ids);
  }, [item, statements, labelStore]);

  const save = useCallback(async (payload: ItemOverridePayload) => {
    if (!item.local_id) return;
    setSaving(true);
    setError(null);
    try {
      await Studio.patchItemOverride(runId, item.local_id, payload);
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setSaving(false);
    }
  }, [item.local_id, onSaved, runId]);

  const handleSave = useCallback(async () => {
    const payload: ItemOverridePayload = {
      labels,
      descriptions,
      aliases: aliasesHe.trim()
        ? {he: aliasesHe.split("·").map((s) => s.trim()).filter(Boolean)}
        : undefined,
    };
    await save(payload);
  }, [aliasesHe, descriptions, labels, save]);

  const toggleExclude = useCallback(async (idx: number) => {
    if (!item.local_id) return;
    const next = new Set(excluded);
    if (next.has(idx)) next.delete(idx); else next.add(idx);
    setExcluded(next);
    setExcludeSaving(true);
    try {
      await Studio.patchItemOverride(runId, item.local_id, {remove_statements: Array.from(next)});
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setExcludeSaving(false);
    }
  }, [excluded, item.local_id, onSaved, runId]);

  const handleReconcile = useCallback(async () => {
    if (!item.local_id) return;
    setReconciling(true);
    setReconcileMsg(null);
    try {
      const res = await Studio.reconcileItem(runId, item.local_id);
      setReconcileMsg(`${res.status}: ${res.qid ?? "—"}${res.message ? ` — ${res.message}` : ""}`);
      onSaved();
    } catch (e) {
      setReconcileMsg(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setReconciling(false);
    }
  }, [item.local_id, onSaved, runId]);

  const applyAutofix = useCallback(async () => {
    if (!item.local_id) return;
    const fixes = (item.ai_verdict as {suggested_fixes?: Array<Record<string, unknown>>} | null)?.suggested_fixes;
    if (!fixes?.length) return;
    setSaving(true);
    try {
      await Studio.applyAiFixes(runId, item.local_id, fixes);
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setSaving(false);
    }
  }, [item.ai_verdict, item.local_id, onSaved, runId]);

  const pushToWikidata = useCallback(async () => {
    if (!item.local_id) return;
    if (uploadTarget === "live") {
      const ok = window.confirm(
        "Push this item to live wikidata.org?\n\nPrefer test.wikidata.org first.",
      );
      if (!ok) return;
    }
    setPushing(true);
    setPushMsg(null);
    try {
      const res = await Studio.pushItem(runId, item.local_id, uploadTarget);
      setPushMsg(`${res.status}${res.qid ? ` → ${res.qid}` : ""}${res.message ? `: ${res.message}` : ""}`);
      onSaved();
    } catch (e) {
      setPushMsg(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setPushing(false);
    }
  }, [item.local_id, onSaved, runId, uploadTarget]);

  const applyAutofixAndPush = useCallback(async () => {
    const fixes = (item.ai_verdict as {suggested_fixes?: Array<Record<string, unknown>>} | null)?.suggested_fixes;
    if (!fixes?.length || !item.local_id) return;
    setSaving(true);
    setPushMsg(null);
    try {
      await Studio.applyAiFixes(runId, item.local_id, fixes);
      setSaving(false);
      await pushToWikidata();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
      setSaving(false);
    }
  }, [item.ai_verdict, item.local_id, pushToWikidata, runId]);

  const toggleAcceptForeign = useCallback(async () => {
    if (!item.local_id || !wikidataQid) return;
    const next = !acceptForeign;
    setAcceptForeign(next);
    await save({
      accept_foreign_modify: next,
      accepted_foreign_qid: next ? wikidataQid : null,
    });
  }, [acceptForeign, item.local_id, save, wikidataQid]);

  const clearOverride = useCallback(async () => {
    if (!item.local_id) return;
    setSaving(true);
    try {
      await Studio.clearOverride(runId, item.local_id);
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setSaving(false);
    }
  }, [item.local_id, onSaved, runId]);

  const pushLabel = uploadTarget === "test" || testMode
    ? "Push to test.wikidata.org"
    : uploadTarget === "live" || moratoriumLifted
      ? "Push to Wikidata now"
      : "Push to test.wikidata.org";

  return (
    <>
      <Glass
        variant="drawer"
        className="fixed right-0 top-0 bottom-0 z-40 w-full max-w-[640px] p-5 overflow-y-auto space-y-4"
        data-testid="wikidata-item-detail-drawer"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="kicker">Wikidata Studio item</div>
            <h3 className="text-lg font-medium">{labelOf(item)}</h3>
            <p className="muted text-xs font-mono mt-1">{item.local_id}</p>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs muted flex items-center gap-1">
              <input type="checkbox" checked={pinned} onChange={(e) => setPinned(e.target.checked)} />
              Pin
            </label>
            {projectId && historyId && (
              <button type="button" className="button-ghost text-xs" onClick={() => setShowHistory((v) => !v)}>
                📜
              </button>
            )}
            <button type="button" className="button-ghost text-xs" onClick={onClose}>Close</button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <WikidataItemDataStatusBadge item={item} />
          {(item.validation_issues ?? []).length > 0 && (
            <ItemValidatorBadge issues={item.validation_issues ?? []} localId={item.local_id} expanded />
          )}
          <UploadOutcomeBadge
            outcome={item.upload_outcome}
            message={item.upload_message}
            at={item.upload_at}
            localId={item.local_id}
            testIdPrefix="wikidata-item"
          />
          <WikidataItemAiVerdictBadge verdict={item.ai_verdict} localId={item.local_id} />
          {onVerify && (
            <button type="button" className="button-ghost text-xs" onClick={onVerify} data-testid="wikidata-item-verify-btn">
              Verify with AI
            </button>
          )}
          {onAutofix && (
            <button type="button" className="button-ghost text-xs" onClick={onAutofix} data-testid="wikidata-item-autofix-btn">
              Autofix with AI
            </button>
          )}
          {(item.ai_verdict as {suggested_fixes?: unknown[]} | null)?.suggested_fixes?.length ? (
            <>
              <button
                type="button"
                className="button-primary text-xs"
                onClick={() => void applyAutofix()}
                disabled={saving}
                data-testid="wikidata-item-apply-fix-btn"
              >
                Apply AI fix
              </button>
              <button
                type="button"
                className="button-primary text-xs"
                onClick={() => void applyAutofixAndPush()}
                disabled={saving || pushing}
                data-testid="wikidata-item-apply-fix-push-btn"
              >
                Apply fix &amp; push
              </button>
            </>
          ) : null}
          <button
            type="button"
            className="button-ghost text-xs"
            onClick={() => void pushToWikidata()}
            disabled={pushing}
            data-testid="wikidata-item-push-btn"
          >
            {pushing ? "Pushing…" : pushLabel}
          </button>
        </div>

        {error && <p className="text-danger text-sm">{error}</p>}
        {pushMsg && <p className="text-xs muted">Push result: {pushMsg}</p>}

        {wikidataQid && (
          <section
            className="space-y-2 border border-amber-500/30 rounded-md p-3 bg-amber-500/5"
            data-testid="wikidata-foreign-accept"
          >
            <h4 className="text-sm font-medium">Existing Wikidata item {wikidataQid}</h4>
            <p className="text-xs muted leading-relaxed">
              By default you may only CREATE new items (after dedup) or UPDATE items
              your Wikidata token created. To modify this QID if it is community-owned,
              explicitly accept below — a duplicate CREATE is never allowed.
            </p>
            <label className="flex items-start gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                className="mt-1"
                checked={acceptForeign}
                disabled={saving}
                onChange={() => void toggleAcceptForeign()}
                data-testid="wikidata-accept-foreign-modify"
              />
              <span>
                I accept modifying <span className="font-mono">{wikidataQid}</span> even
                if I did not create it
              </span>
            </label>
          </section>
        )}

        <AiVerdictReasoningCard verdict={item.ai_verdict} />

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
          <h4 className="text-sm font-medium">Aliases (Hebrew, · separated)</h4>
          <input
            value={aliasesHe}
            onChange={(e) => setAliasesHe(e.target.value)}
            className="input-glass text-sm w-full"
          />
        </section>

        <section className="space-y-2 border-t border-white/5 pt-3">
          <div className="flex items-center justify-between gap-2">
            <h4 className="text-sm font-medium">Statements ({statements.length})</h4>
            {excludeSaving && <span className="text-xs text-biu-sky animate-pulse">Saving…</span>}
          </div>
          <ul className="space-y-2">
            {statements.map((s, i) => {
              const isExcluded = excluded.has(i);
              const prop = s.property ?? s.property_id;
              return (
                <GlassPill as="li" key={i} className={`px-3 py-2 text-sm ${isExcluded ? "opacity-50" : ""}`}>
                  <div className={`flex items-baseline gap-2 flex-wrap ${isExcluded ? "line-through" : ""}`}>
                    <PropertyPill p={prop} label={s.property_label} store={labelStore} />
                    <ValueRendering snak={s} store={labelStore} />
                    <span className="ml-auto shrink-0">
                      {isExcluded ? (
                        <button type="button" onClick={() => void toggleExclude(i)} className="text-[11px] text-biu-sky hover:underline">
                          Undo
                        </button>
                      ) : (
                        <button type="button" onClick={() => void toggleExclude(i)} className="text-[11px] muted hover:text-danger transition">
                          ✗ Exclude
                        </button>
                      )}
                    </span>
                  </div>
                </GlassPill>
              );
            })}
          </ul>
        </section>

        <section className="space-y-2 border-t border-white/5 pt-3">
          <h4 className="text-sm font-medium">Reconcile / Compare</h4>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="button-ghost text-xs"
              disabled={reconciling}
              onClick={() => void handleReconcile()}
              data-testid="wikidata-item-reconcile-btn"
            >
              {reconciling ? "Checking…" : "Reconcile with Wikidata"}
            </button>
            {wikidataQid && (
              <button
                type="button"
                className="button-ghost text-xs"
                onClick={() => setCompareOpen(true)}
                data-testid="wikidata-item-compare-btn"
              >
                Compare with {wikidataQid}
              </button>
            )}
          </div>
          {reconcileMsg && <p className="text-xs muted">{reconcileMsg}</p>}
        </section>

        <div className="flex flex-wrap gap-2 pt-2">
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
          <button
            type="button"
            className="button-ghost text-sm text-warn"
            onClick={() => void clearOverride()}
            disabled={saving}
            data-testid="wikidata-item-clear-override-btn"
          >
            Clear override
          </button>
        </div>

        {showHistory && projectId && historyId && (
          <HistoryTimeline
            projectId={projectId}
            entityType="wikidata_override"
            entityId={historyId}
          />
        )}
      </Glass>

      {compareOpen && wikidataQid && item.local_id && (
        <WikidataComparePanel
          runId={runId}
          item={item}
          qid={wikidataQid}
          approvedOnly={approvedOnly}
          onClose={() => setCompareOpen(false)}
          onApplied={() => {
            setCompareOpen(false);
            onSaved();
          }}
        />
      )}
    </>
  );
}
