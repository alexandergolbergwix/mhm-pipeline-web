import {useCallback, useEffect, useMemo, useRef, useState} from "react";

import {ApiError} from "@/api/client";
import {HmoStudioItems, type HmoStudioItem} from "@/api/hmoStudioItems";
import {type RunJobSnapshot} from "@/api/runJobs";
import {SectionExportMenu} from "@/components/export/SectionExportMenu";
import {SectionImportButton} from "@/components/import/SectionImportButton";
import {Glass} from "@/components/glass";
import {HmoAuthorityConflictPanel} from "@/components/hmo/HmoAuthorityConflictPanel";
import {HmoItemDetailDrawer} from "@/components/hmo/HmoItemDetailDrawer";
import {HmoItemTable, type HmoItemTableQuery} from "@/components/hmo/HmoItemTable";
import {HmoItemVerificationModal} from "@/components/hmo/HmoItemVerificationModal";
import {RuleVerificationPanel} from "@/components/hmo/RuleVerificationPanel";
import {ItemBuildPanel} from "@/components/hmo/ItemBuildPanel";
import {ItemUploadPanel} from "@/components/hmo/ItemUploadPanel";
import {JobProgressInline} from "@/components/jobs/JobProgressInline";
import {useRunJobAttachment} from "@/hooks/useRunJobAttachment";
import {isJobActive, useRunJobs} from "@/stores/runJobs";
import {
  createThrottledProgressRefresh,
  jobProcessedCount,
} from "@/utils/throttledProgressRefresh";
import {patchHmoItemsFromUploadOutcomes} from "@/utils/studioUploadProgress";
import {ensureRunJob} from "@/utils/waitForRunJob";

export interface HmoItemsPanelProps {
  runId: string;
  projectId?: string;
  buildPresent: boolean;
  /** True until the page's item-status probe resolves — show a loader, not "Build the RDF graph first". */
  statusPending?: boolean;
  /** Job tray "View" landed with ?job=<verify job id> — reopen the modal (W-141). */
  reopenVerify?: {jobId: string; actionId?: string; itemIds?: string[]} | null;
  onReopenVerifyHandled?: () => void;
  refreshToken?: number;
  rdfPresent?: boolean;
  wikibaseConfigured?: boolean;
  onLifecycleChange?: () => void;
}

export function HmoItemsPanel({
  runId,
  projectId,
  buildPresent,
  statusPending = false,
  reopenVerify = null,
  onReopenVerifyHandled,
  refreshToken,
  rdfPresent = false,
  wikibaseConfigured = false,
  onLifecycleChange,
}: HmoItemsPanelProps) {
  const [items, setItems] = useState<HmoStudioItem[]>([]);
  const [tableTotal, setTableTotal] = useState(0);
  const [tableFacets, setTableFacets] = useState<Record<string, Record<string, number>>>({});
  const [tablePage, setTablePage] = useState(1);
  const [tableQuery, setTableQuery] = useState<HmoItemTableQuery>({
    search: "",
    sortKey: "label",
    sortDir: "asc",
    colFilters: {},
  });
  // Start in the loading state so the first paint shows the loader instead
  // of a flashed empty table (the 18k-item list request takes a while).
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openItem, setOpenItem] = useState<HmoStudioItem | null>(null);
  const [verifyOpen, setVerifyOpen] = useState(false);
  const [verifyIds, setVerifyIds] = useState<string[] | undefined>(undefined);
  const [verifyActionId, setVerifyActionId] = useState<string | undefined>(undefined);
  const [rulePanelOpen, setRulePanelOpen] = useState(false);
  const [decisionFeedback, setDecisionFeedback] = useState<string | null>(null);
  const [approvingVisible, setApprovingVisible] = useState(false);
  const [approveJob, setApproveJob] = useState<RunJobSnapshot | null>(null);
  const upsertJob = useRunJobs((s) => s.upsertJob);

  // Server-driven table state: one page of rows + a light id set for the
  // bulk actions. The browser never holds the full corpus.
  const [idEntries, setIdEntries] = useState<Array<{local_id: string; wikibase_id: string | null; approved: boolean | null}>>([]);

  const filteredIds = useMemo(
    () => idEntries.map((e) => e.local_id),
    [idEntries],
  );

  const autofixItemIds = useMemo(
    () => idEntries.filter((e) => Boolean(e.wikibase_id?.trim())).map((e) => e.local_id),
    [idEntries],
  );

  const pendingVisibleIds = useMemo(
    () => idEntries.filter((e) => e.approved !== true).map((e) => e.local_id),
    [idEntries],
  );

  const failedLocalIds = useMemo(
    () => items.filter((i) => i.upload_outcome === "failed").map((i) => i.local_id),
    [items],
  );

  const openVerify = useCallback((itemIds: string[], actionId?: string) => {
    setVerifyIds(itemIds);
    setVerifyActionId(actionId);
    setVerifyOpen(true);
  }, []);

  // Job tray "View" → reopen the verification modal for a running job.
  const handledReopenRef = useRef<string | null>(null);
  useEffect(() => {
    if (!reopenVerify || handledReopenRef.current === reopenVerify.jobId) return;
    handledReopenRef.current = reopenVerify.jobId;
    openVerify(reopenVerify.itemIds ?? [], reopenVerify.actionId);
    onReopenVerifyHandled?.();
  }, [onReopenVerifyHandled, openVerify, reopenVerify]);

  const load = useCallback(async (opts?: {silent?: boolean}) => {
    if (!buildPresent) {
      setLoading(false);
      return;
    }
    const silent = Boolean(opts?.silent);
    if (!silent) setLoading(true);
    setError(null);
    try {
      const res = await HmoStudioItems.page(runId, {
        page: tablePage,
        pageSize: 25,
        q: tableQuery.search,
        sort: tableQuery.sortKey,
        dir: tableQuery.sortDir,
        filters: tableQuery.colFilters as Record<string, string[]>,
      });
      setItems(res.items);
      setTableTotal(res.total);
      setTableFacets(res.facets);
      setOpenItem((prev) => {
        if (!prev) return prev;
        return res.items.find((i) => i.local_id === prev.local_id) ?? prev;
      });
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      if (!silent) setLoading(false);
    }
  }, [buildPresent, runId, tablePage, tableQuery]);

  // Light id-set refresh (bulk actions + verify scope) — debounced.
  useEffect(() => {
    if (!buildPresent) return;
    const t = window.setTimeout(() => {
      HmoStudioItems.filteredIds(runId, {
        q: tableQuery.search,
        filters: tableQuery.colFilters as Record<string, string[]>,
      })
        .then((res) => setIdEntries(res.entries))
        .catch(() => { /* transient — the page fetch surfaces errors */ });
    }, 400);
    return () => window.clearTimeout(t);
  }, [buildPresent, runId, tableQuery]);

  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  const handleLifecycleRefresh = useCallback(() => {
    void load({silent: items.length > 0});
    onLifecycleChange?.();
  }, [items.length, load, onLifecycleChange]);

  const applyUploadOutcomes = useCallback((outcomes: Parameters<typeof patchHmoItemsFromUploadOutcomes>[1]) => {
    if (!outcomes.length) return;
    setItems((prev) => patchHmoItemsFromUploadOutcomes(prev, outcomes));
    setOpenItem((prev) => {
      if (!prev) return prev;
      const patched = patchHmoItemsFromUploadOutcomes([prev], outcomes);
      return patched[0] ?? prev;
    });
  }, []);

  const approveTableRefreshRef = useRef(createThrottledProgressRefresh());

  const {setTrackedJobId, ensureJobPolling} = useRunJobAttachment(
    runId,
    "hmo_item_bulk_approve",
    (j) => {
      setApproveJob(j);
      if (isJobActive(j.status)) {
        if (approveTableRefreshRef.current.shouldRefresh(jobProcessedCount(j))) {
          void load({silent: true});
        }
      }
      if (j.status === "succeeded") {
        const approved = Number(j.result?.approved ?? 0);
        const unchanged = Number(j.result?.unchanged ?? 0);
        const failed = Number(j.result?.failed ?? 0);
        void load({silent: true});
        setDecisionFeedback(
          failed > 0
            ? `Approved ${approved}, already approved ${unchanged}, failed ${failed}.`
            : `Approved ${approved} entr${approved === 1 ? "y" : "ies"}`
              + (unchanged ? ` (${unchanged} already approved).` : "."),
        );
        setApprovingVisible(false);
      }
      if (j.status === "failed" || j.status === "cancelled") {
        void load({silent: true});
        setDecisionFeedback(
          j.status === "cancelled"
            ? "Bulk approve cancelled."
            : (j.error ?? "Bulk approve failed. Refresh and retry."),
        );
        setApprovingVisible(false);
      }
    },
  );

  const handleToggleApproved = useCallback(async (item: HmoStudioItem, next: boolean | null) => {
    setDecisionFeedback(null);
    try {
      await HmoStudioItems.patchOverride(runId, item.local_id, {approved: next});
      await load({silent: true});
      setDecisionFeedback(next === true ? "Entry marked approved." : next === false ? "Entry marked rejected." : "Entry returned to pending review.");
    } catch (e) {
      setDecisionFeedback(e instanceof ApiError ? e.detail : "We could not save this decision. Nothing was changed.");
    }
  }, [load, runId]);

  const approveAllVisible = useCallback(async () => {
    if (!pendingVisibleIds.length) return;
    const ok = window.confirm(
      `Approve all ${pendingVisibleIds.length} visible entr${pendingVisibleIds.length === 1 ? "y" : "ies"} that are not already approved?`,
    );
    if (!ok) return;
    setApprovingVisible(true);
    setDecisionFeedback(null);
    try {
      const started = await ensureRunJob(runId, "hmo_item_bulk_approve", {
        local_ids: pendingVisibleIds,
        approved: true,
      });
      approveTableRefreshRef.current.reset();
      upsertJob(started);
      setApproveJob(started);
      setTrackedJobId(started.id);
      ensureJobPolling();
      if (!isJobActive(started.status)) {
        setApprovingVisible(false);
      }
    } catch (e) {
      setDecisionFeedback(e instanceof ApiError ? e.detail : "Bulk approve failed. Refresh and retry.");
      setApprovingVisible(false);
    }
  }, [ensureJobPolling, pendingVisibleIds, runId, setTrackedJobId, upsertJob]);

  const approveBusy = approvingVisible || (approveJob != null && isJobActive(approveJob.status));
  const showTable = buildPresent && (items.length > 0 || !loading);
  // First load (or a load that cleared the table) — or the item-status
  // probe hasn't answered yet: show a loader instead of the misleading
  // "0 resolved items / Build the RDF graph first" state (2026-09-18).
  const firstLoad = statusPending || (buildPresent && loading && items.length === 0);

  return (
    <Glass as="section" className="p-6 space-y-4" data-testid="hmo-items-panel">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <div className="kicker">Wikibase Items</div>
          <h3 className="text-lg font-medium" data-testid="hmo-items-heading">
            {firstLoad ? "Loading resolved items…" : `${items.length} resolved item${items.length === 1 ? "" : "s"}`}
          </h3>
          <p className="muted text-sm mt-1">
            Review table for this run&apos;s item build. <b>Data status</b> shows whether each row is
            new, already on the wiki and due for a reupload, or was updated in the last push.
            <b> Last push</b> is the latest upload attempt (create, adopt, update, skip, or failed).
            Open a row for overrides, AI verify, and single-item push.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <SectionExportMenu section="wikibase_items" runId={runId} availableFormats={["json", "csv"]} />
          <SectionImportButton section="wikibase_items" runId={runId} accept=".json" onComplete={() => void load()} />
          <button type="button" className="button-ghost text-xs" onClick={() => void load()} disabled={loading}>
            Refresh
          </button>
          <button
            type="button"
            className="button-ghost text-xs"
            disabled={firstLoad || !filteredIds.length}
            data-testid="hmo-items-verify-ai"
            onClick={() => openVerify(filteredIds, "audit_hmo_wikibase_item")}
          >
            Verify with AI {firstLoad ? "…" : `(${filteredIds.length})`}
          </button>
          <button
            type="button"
            className="button-ghost text-xs"
            disabled={firstLoad || !filteredIds.length}
            data-testid="hmo-items-verify-rules"
            onClick={() => setRulePanelOpen((v) => !v)}
          >
            {rulePanelOpen ? "Hide rules check" : `Rules based verification ${firstLoad ? "…" : `(${filteredIds.length})`}`}
          </button>          <button
            type="button"
            className="button-ghost text-xs"
            disabled={firstLoad || !autofixItemIds.length}
            title="Compare each item's live Wikibase entity against the build and propose fixes you can apply per row (requires a QID)."
            data-testid="hmo-items-autofix-ai"
            onClick={() => openVerify(autofixItemIds, "autofix_hmo_wikibase_item")}
          >
            Autofix with AI {firstLoad ? "…" : `(${autofixItemIds.length})`}
          </button>
          <button
            type="button"
            className="button-primary text-xs"
            disabled={firstLoad || !pendingVisibleIds.length || approveBusy}
            title="Approve every currently filtered row that is not already approved (runs as a background job)."
            data-testid="hmo-items-approve-visible"
            onClick={() => void approveAllVisible()}
          >
            {approveBusy
              ? "Approving…"
              : firstLoad
                ? "Approve all visible…"
                : `Approve all visible (${pendingVisibleIds.length})`}
          </button>
        </div>
      </div>

      <HmoAuthorityConflictPanel
        runId={runId}
        refreshToken={refreshToken}
        onResolved={handleLifecycleRefresh}
      />

      <div className="space-y-3 border-b border-white/5 pb-4" data-testid="hmo-item-lifecycle-bar">
        <ItemBuildPanel
          runId={runId}
          rdfPresent={rdfPresent}
          compact
          onBuilt={handleLifecycleRefresh}
        />
        <ItemUploadPanel
          runId={runId}
          wikibaseConfigured={wikibaseConfigured}
          refreshToken={refreshToken}
          compact
          failedLocalIds={failedLocalIds}
          onUploaded={handleLifecycleRefresh}
          onUploadOutcomes={applyUploadOutcomes}
        />
      </div>

      {decisionFeedback && <p className="text-sm text-biu-sky" role="status">{decisionFeedback}</p>}
      {rulePanelOpen && buildPresent && (
        <RuleVerificationPanel
          runId={runId}
          onClose={() => setRulePanelOpen(false)}
          onApproved={() => void load({silent: true})}
        />
      )}
      {approveJob && (
        <JobProgressInline
          job={approveJob}
          labels={{
            running: "Approving visible items…",
            succeeded: "Approve complete:",
            failed: "Approve failed:",
            cancelled: "Approve cancelled:",
          }}
        />
      )}

      {!buildPresent && !statusPending && (
        <p className="muted text-sm">Build items above before the review table loads.</p>
      )}
      {error && <p className="text-danger text-sm">{error}</p>}
      {showTable && (
        <HmoItemTable
          items={items}
          total={tableTotal}
          page={tablePage}
          pageCount={Math.max(1, Math.ceil(tableTotal / 25))}
          facets={tableFacets}
          query={tableQuery}
          onQueryChange={(q) => { setTablePage(1); setTableQuery(q); }}
          onPageChange={setTablePage}
          onOpenItem={setOpenItem}
          onToggleApproved={(item, next) => void handleToggleApproved(item, next)}
        />
      )}
      {firstLoad && (
        <div
          className="flex items-center justify-center gap-3 py-10"
          data-testid="hmo-items-loading"
          role="status"
          aria-live="polite"
        >
          <span
            className="animate-spin inline-block w-5 h-5 border-2 border-current border-t-transparent rounded-full text-biu-sky"
            aria-hidden="true"
          />
          <span className="muted text-sm">Loading resolved items…</span>
        </div>
      )}

      {openItem && (
        <HmoItemDetailDrawer
          runId={runId}
          projectId={projectId}
          item={openItem}
          allItems={items}
          onClose={() => setOpenItem(null)}
          onSaved={() => void load({silent: true})}
          onVerify={() => openVerify([openItem.local_id], "audit_hmo_wikibase_item")}
          onAutofix={
            openItem.wikibase_id?.trim()
              ? () => openVerify([openItem.local_id], "autofix_hmo_wikibase_item")
              : undefined
          }
        />
      )}

      {verifyOpen && (
        <HmoItemVerificationModal
          runId={runId}
          scopeLabel={verifyIds?.length === 1 ? `Item ${verifyIds[0]}` : `${verifyIds?.length ?? 0} items`}
          itemIds={verifyIds}
          initialActionId={verifyActionId}
          onVerdictsLanded={() => void load({silent: true})}
          onClose={() => {
            setVerifyOpen(false);
            setVerifyActionId(undefined);
          }}
        />
      )}
    </Glass>
  );
}
