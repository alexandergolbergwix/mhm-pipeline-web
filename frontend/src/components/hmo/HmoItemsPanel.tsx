import {useCallback, useEffect, useMemo, useState} from "react";

import {ApiError} from "@/api/client";
import {HmoStudioItems, type HmoStudioItem} from "@/api/hmoStudioItems";
import {type RunJobSnapshot} from "@/api/runJobs";
import {SectionExportMenu} from "@/components/export/SectionExportMenu";
import {SectionImportButton} from "@/components/import/SectionImportButton";
import {Glass} from "@/components/glass";
import {HmoItemDetailDrawer} from "@/components/hmo/HmoItemDetailDrawer";
import {HmoItemTable} from "@/components/hmo/HmoItemTable";
import {HmoItemVerificationModal} from "@/components/hmo/HmoItemVerificationModal";
import {ItemBuildPanel} from "@/components/hmo/ItemBuildPanel";
import {ItemUploadPanel} from "@/components/hmo/ItemUploadPanel";
import {JobProgressInline} from "@/components/jobs/JobProgressInline";
import {useRunJobAttachment} from "@/hooks/useRunJobAttachment";
import {isJobActive, useRunJobs} from "@/stores/runJobs";
import {ensureRunJob} from "@/utils/waitForRunJob";

export interface HmoItemsPanelProps {
  runId: string;
  projectId?: string;
  buildPresent: boolean;
  refreshToken?: number;
  rdfPresent?: boolean;
  wikibaseConfigured?: boolean;
  onLifecycleChange?: () => void;
}

export function HmoItemsPanel({
  runId,
  projectId,
  buildPresent,
  refreshToken,
  rdfPresent = false,
  wikibaseConfigured = false,
  onLifecycleChange,
}: HmoItemsPanelProps) {
  const [items, setItems] = useState<HmoStudioItem[]>([]);
  const [filteredIds, setFilteredIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openItem, setOpenItem] = useState<HmoStudioItem | null>(null);
  const [verifyOpen, setVerifyOpen] = useState(false);
  const [verifyIds, setVerifyIds] = useState<string[] | undefined>(undefined);
  const [verifyActionId, setVerifyActionId] = useState<string | undefined>(undefined);
  const [decisionFeedback, setDecisionFeedback] = useState<string | null>(null);
  const [approvingVisible, setApprovingVisible] = useState(false);
  const [approveJob, setApproveJob] = useState<RunJobSnapshot | null>(null);
  const upsertJob = useRunJobs((s) => s.upsertJob);

  const autofixItemIds = useMemo(() => {
    const visible = new Set(filteredIds);
    return items
      .filter((item) => visible.has(item.local_id) && Boolean(item.wikibase_id?.trim()))
      .map((item) => item.local_id);
  }, [filteredIds, items]);

  const pendingVisibleIds = useMemo(() => {
    const visible = new Set(filteredIds);
    return items
      .filter((item) => visible.has(item.local_id) && item.approved !== true)
      .map((item) => item.local_id);
  }, [filteredIds, items]);

  const openVerify = useCallback((itemIds: string[], actionId?: string) => {
    setVerifyIds(itemIds);
    setVerifyActionId(actionId);
    setVerifyOpen(true);
  }, []);

  const load = useCallback(async () => {
    if (!buildPresent) return;
    setLoading(true);
    setError(null);
    try {
      const res = await HmoStudioItems.list(runId);
      setItems(res.items);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setLoading(false);
    }
  }, [buildPresent, runId]);

  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  const {setTrackedJobId, ensureJobPolling} = useRunJobAttachment(
    runId,
    "hmo_item_bulk_approve",
    (j) => {
      setApproveJob(j);
      if (j.status === "succeeded") {
        const approved = Number(j.result?.approved ?? 0);
        const unchanged = Number(j.result?.unchanged ?? 0);
        const failed = Number(j.result?.failed ?? 0);
        void load();
        setDecisionFeedback(
          failed > 0
            ? `Approved ${approved}, already approved ${unchanged}, failed ${failed}.`
            : `Approved ${approved} entr${approved === 1 ? "y" : "ies"}`
              + (unchanged ? ` (${unchanged} already approved).` : "."),
        );
        setApprovingVisible(false);
      }
      if (j.status === "failed" || j.status === "cancelled") {
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
      await load();
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

  return (
    <Glass as="section" className="p-6 space-y-4" data-testid="hmo-items-panel">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <div className="kicker">Wikibase Items</div>
          <h3 className="text-lg font-medium">{items.length} resolved item{items.length === 1 ? "" : "s"}</h3>
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
            disabled={!filteredIds.length}
            data-testid="hmo-items-verify-ai"
            onClick={() => openVerify(filteredIds, "audit_hmo_wikibase_item")}
          >
            Verify with AI ({filteredIds.length})
          </button>
          <button
            type="button"
            className="button-ghost text-xs"
            disabled={!autofixItemIds.length}
            title="Compare each item's live Wikibase entity against the build and propose fixes you can apply per row (requires a QID)."
            data-testid="hmo-items-autofix-ai"
            onClick={() => openVerify(autofixItemIds, "autofix_hmo_wikibase_item")}
          >
            Autofix with AI ({autofixItemIds.length})
          </button>
          <button
            type="button"
            className="button-primary text-xs"
            disabled={!pendingVisibleIds.length || approveBusy}
            title="Approve every currently filtered row that is not already approved (runs as a background job)."
            data-testid="hmo-items-approve-visible"
            onClick={() => void approveAllVisible()}
          >
            {approveBusy
              ? "Approving…"
              : `Approve all visible (${pendingVisibleIds.length})`}
          </button>
        </div>
      </div>

      <div className="space-y-3 border-b border-white/5 pb-4" data-testid="hmo-item-lifecycle-bar">
        <ItemBuildPanel
          runId={runId}
          rdfPresent={rdfPresent}
          compact
          onBuilt={onLifecycleChange}
        />
        <ItemUploadPanel
          runId={runId}
          wikibaseConfigured={wikibaseConfigured}
          refreshToken={refreshToken}
          compact
          onUploaded={onLifecycleChange}
        />
      </div>

      {decisionFeedback && <p className="text-sm text-biu-sky" role="status">{decisionFeedback}</p>}
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

      {!buildPresent && (
        <p className="muted text-sm">Build items above before the review table loads.</p>
      )}
      {error && <p className="text-danger text-sm">{error}</p>}
      {buildPresent && !loading && (
        <HmoItemTable
          items={items}
          onFilteredChange={setFilteredIds}
          onOpenItem={setOpenItem}
          onToggleApproved={(item, next) => void handleToggleApproved(item, next)}
        />
      )}
      {loading && <p className="muted text-sm">Loading items…</p>}

      {openItem && (
        <HmoItemDetailDrawer
          runId={runId}
          projectId={projectId}
          item={openItem}
          allItems={items}
          onClose={() => setOpenItem(null)}
          onSaved={() => void load()}
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
          onVerdictsLanded={() => void load()}
          onClose={() => {
            setVerifyOpen(false);
            setVerifyActionId(undefined);
          }}
        />
      )}
    </Glass>
  );
}
