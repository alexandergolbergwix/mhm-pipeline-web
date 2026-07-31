import {useCallback, useEffect, useMemo, useRef, useState} from "react";
import {useSearchParams} from "react-router-dom";

import {ApiError} from "@/api/client";
import {type RunJobSnapshot} from "@/api/runJobs";
import {
  Studio,
  fetchAllStudioItems,
  type StudioBuild,
  type StudioItem,
  type WikidataUploadTarget,
} from "@/api/wikidataStudio";
import {SectionExportMenu} from "@/components/export/SectionExportMenu";
import {Glass, GlassPill} from "@/components/glass";
import {JobProgressInline} from "@/components/jobs/JobProgressInline";
import {LoadingOverlay} from "@/components/LoadingOverlay";
import {WikidataItemDetailDrawer} from "@/components/wikidata/WikidataItemDetailDrawer";
import {WikidataItemTable} from "@/components/wikidata/WikidataItemTable";
import {WikidataUploadPanel} from "@/components/wikidata/WikidataUploadPanel";
import {WikidataVerificationModal} from "@/components/wikidata/WikidataVerificationModal";
import {useRunJobAttachment} from "@/hooks/useRunJobAttachment";
import {isJobActive, useRunJobs} from "@/stores/runJobs";
import {
  createThrottledProgressRefresh,
  jobProcessedCount,
} from "@/utils/throttledProgressRefresh";
import {patchWikidataItemsFromUploadOutcomes} from "@/utils/studioUploadProgress";
import {ensureRunJob, loadStudioBuild} from "@/utils/waitForRunJob";
import {useLabelStore} from "@/api/wikidataLabels";

export interface WikidataItemsPanelProps {
  runId: string;
  source: "legacy" | "canonical";
  projectId?: string;
  approvedOnly: boolean;
  uploadApprovedOnly: boolean;
  forceRebuild: boolean;
  onApprovedOnlyChange: (v: boolean) => void;
  onForceRebuildChange: (v: boolean) => void;
  onUploadApprovedOnlyChange: (v: boolean) => void;
  onBuildLoaded?: (build: StudioBuild) => void;
}

export function WikidataItemsPanel({
  runId,
  source,
  projectId,
  approvedOnly,
  uploadApprovedOnly,
  forceRebuild,
  onApprovedOnlyChange,
  onForceRebuildChange,
  onUploadApprovedOnlyChange,
  onBuildLoaded,
}: WikidataItemsPanelProps) {
  const [build, setBuild] = useState<StudioBuild | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [buildProgress, setBuildProgress] = useState<string | null>(null);
  const [filteredIds, setFilteredIds] = useState<string[]>([]);
  const [openItem, setOpenItem] = useState<StudioItem | null>(null);
  const [verifyOpen, setVerifyOpen] = useState(false);
  const [verifyIds, setVerifyIds] = useState<string[] | undefined>(undefined);
  const [verifyActionId, setVerifyActionId] = useState<string | undefined>(undefined);
  const [uploadTarget, setUploadTarget] = useState<WikidataUploadTarget>("dry_run");
  const [moratoriumLifted, setMoratoriumLifted] = useState(false);
  const [testMode, setTestMode] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const [approvingVisible, setApprovingVisible] = useState(false);
  const [approveJob, setApproveJob] = useState<RunJobSnapshot | null>(null);
  const [approveFeedback, setApproveFeedback] = useState<string | null>(null);
  const [studioBuildJob, setStudioBuildJob] = useState<RunJobSnapshot | null>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const labelStore = useLabelStore();
  const upsertJob = useRunJobs((s) => s.upsertJob);

  const autofixItemIds = useMemo(() => {
    const visible = new Set(filteredIds);
    return (build?.items ?? [])
      .filter((item) => item.local_id && visible.has(item.local_id) && Boolean(item.existing_qid?.trim()))
      .map((item) => item.local_id as string);
  }, [build?.items, filteredIds]);

  const pendingVisibleIds = useMemo(() => {
    const visible = new Set(filteredIds);
    return (build?.items ?? [])
      .filter((item) => item.local_id && visible.has(item.local_id) && item.approved !== true)
      .map((item) => item.local_id as string);
  }, [build?.items, filteredIds]);

  const loadItems = useCallback(async (opts?: {silent?: boolean}) => {
    const silent = Boolean(opts?.silent);
    if (!silent) {
      setLoading(true);
      setBuildProgress("Loading items…");
    }
    setError(null);
    try {
      const fetchPage = () => fetchAllStudioItems(runId, {
        approvedOnly,
        source,
        forceRebuild: false,
      });
      const result = await loadStudioBuild(runId, fetchPage, {
        onProgress: (message) => {
          if (!silent) setBuildProgress(message);
        },
      }) as StudioBuild;
      setBuild(result);
      if (result.property_labels) labelStore.seed(result.property_labels);
      onBuildLoaded?.(result);
      setRefreshToken((t) => t + 1);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      if (!silent) {
        setLoading(false);
        setBuildProgress(null);
      }
    }
  }, [approvedOnly, labelStore, onBuildLoaded, runId, source]);

  const approveTableRefreshRef = useRef(createThrottledProgressRefresh());

  const {
    setTrackedJobId: setStudioBuildTrackedId,
    ensureJobPolling: ensureStudioBuildPolling,
  } = useRunJobAttachment(runId, "wikidata_studio_build", (j) => {
    setStudioBuildJob(j);
    // Build caches the full corpus only at finish — mid-run reload would
    // re-fetch the previous build. Refresh on terminal states below.
    if (j.status === "succeeded") {
      void loadItems();
    }
    if (j.status === "failed" || j.status === "cancelled") {
      setError(j.error ?? (j.status === "cancelled" ? "Build cancelled." : "Build failed."));
      setLoading(false);
    }
  });

  const refresh = useCallback(async (opts?: {nextForceRebuild?: boolean}) => {
    const force = opts?.nextForceRebuild ?? forceRebuild;
    if (force) {
      setError(null);
      setBuildProgress("Starting fresh build…");
      try {
        const job = await ensureRunJob(runId, "wikidata_studio_build", {
          approved_only: approvedOnly,
          force_rebuild: true,
          source,
        });
        upsertJob(job);
        setStudioBuildJob(job);
        setStudioBuildTrackedId(job.id);
        ensureStudioBuildPolling();
        if (isJobActive(job.status)) {
          setBuildProgress(null);
          return;
        }
      } catch (e) {
        setError(e instanceof ApiError ? e.detail : String(e));
        setBuildProgress(null);
        return;
      }
    }
    await loadItems();
  }, [
    approvedOnly, ensureStudioBuildPolling, forceRebuild, loadItems, runId,
    setStudioBuildTrackedId, source, upsertJob,
  ]);

  useEffect(() => {
    void refresh();
  }, [runId, approvedOnly, source]); // eslint-disable-line react-hooks/exhaustive-deps

  const openVerify = useCallback((itemIds: string[], actionId?: string) => {
    setVerifyIds(itemIds);
    setVerifyActionId(actionId);
    setVerifyOpen(true);
  }, []);

  // The job tray's "View" links to `?job=<id>` for jobs whose progress lives in
  // a modal. Reopen it once so the curator lands on the live run they clicked,
  // then drop the param so a later close does not immediately reopen it.
  const [searchParams, setSearchParams] = useSearchParams();
  const deepLinkedJobId = searchParams.get("job");
  useEffect(() => {
    if (!deepLinkedJobId) return;
    setVerifyIds(undefined);
    setVerifyActionId(undefined);
    setVerifyOpen(true);
    const next = new URLSearchParams(searchParams);
    next.delete("job");
    setSearchParams(next, {replace: true});
  }, [deepLinkedJobId]); // eslint-disable-line react-hooks/exhaustive-deps

  const {setTrackedJobId, ensureJobPolling} = useRunJobAttachment(
    runId,
    "wikidata_item_bulk_approve",
    (j) => {
      setApproveJob(j);
      if (isJobActive(j.status)) {
        if (approveTableRefreshRef.current.shouldRefresh(jobProcessedCount(j))) {
          void loadItems({silent: true});
        }
      }
      if (j.status === "succeeded") {
        const approved = Number(j.result?.approved ?? 0);
        const unchanged = Number(j.result?.unchanged ?? 0);
        const failed = Number(j.result?.failed ?? 0);
        void loadItems({silent: true});
        setApproveFeedback(
          failed > 0
            ? `Approved ${approved}, already approved ${unchanged}, failed ${failed}.`
            : `Approved ${approved} item${approved === 1 ? "" : "s"}`
              + (unchanged ? ` (${unchanged} already approved).` : "."),
        );
        setApprovingVisible(false);
      }
      if (j.status === "failed" || j.status === "cancelled") {
        void loadItems({silent: true});
        setApproveFeedback(
          j.status === "cancelled"
            ? "Bulk approve cancelled."
            : (j.error ?? "Bulk approve failed. Refresh and retry."),
        );
        setApprovingVisible(false);
      }
    },
  );

  const applyUploadOutcomes = useCallback((outcomes: Parameters<typeof patchWikidataItemsFromUploadOutcomes>[1]) => {
    if (!outcomes.length) return;
    setBuild((prev) => {
      if (!prev) return prev;
      const items = patchWikidataItemsFromUploadOutcomes(prev.items, outcomes);
      if (items === prev.items) return prev;
      return {...prev, items};
    });
    setOpenItem((prev) => {
      if (!prev) return prev;
      return patchWikidataItemsFromUploadOutcomes([prev], outcomes)[0] ?? prev;
    });
  }, []);

  const handleToggleApproved = useCallback(async (item: StudioItem, next: boolean) => {
    if (!item.local_id) return;
    await Studio.patchItemOverride(runId, item.local_id, {approved: next});
    await refresh();
  }, [refresh, runId]);

  const approveAllVisible = useCallback(async () => {
    if (!pendingVisibleIds.length) return;
    const ok = window.confirm(
      `Approve all ${pendingVisibleIds.length} visible item${pendingVisibleIds.length === 1 ? "" : "s"} that are not already approved?`,
    );
    if (!ok) return;
    setApprovingVisible(true);
    setApproveFeedback(null);
    try {
      const started = await ensureRunJob(runId, "wikidata_item_bulk_approve", {
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
      setApproveFeedback(e instanceof ApiError ? e.detail : "Bulk approve failed. Refresh and retry.");
      setApprovingVisible(false);
    }
  }, [ensureJobPolling, pendingVisibleIds, runId, setTrackedJobId, upsertJob]);

  const approveBusy = approvingVisible || (approveJob != null && isJobActive(approveJob.status));

  const handleImport = useCallback(async (file: File) => {
    await Studio.importItems(runId, file);
    await refresh();
  }, [refresh, runId]);

  const buildPresent = (build?.summary.total_items ?? 0) > 0;

  return (
    <Glass as="section" className="p-6 space-y-4 relative" data-testid="wikidata-items-panel">
      {loading && (
        <LoadingOverlay message="Loading Wikidata items…" detail={buildProgress} className="rounded-xl z-30" />
      )}

      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <div className="kicker">Wikidata Items</div>
          <h3 className="text-lg font-medium">
            {build?.summary.total_items ?? 0} item{(build?.summary.total_items ?? 0) === 1 ? "" : "s"}
          </h3>
          <p className="muted text-sm mt-1">
            These records come from the reviewed HMO Wikibase catalogue. Review, approve, preview the
            Wikidata changes, then publish only when the result is ready.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <a
            href={Studio.exportItemsUrl(runId, "json")}
            download
            className="button-ghost text-xs"
            data-testid="wikidata-items-export-json"
          >
            Export JSON
          </a>
          <a
            href={Studio.exportItemsUrl(runId, "csv")}
            download
            className="button-ghost text-xs"
            data-testid="wikidata-items-export-csv"
          >
            Export CSV
          </a>
          <button
            type="button"
            className="button-ghost text-xs"
            onClick={() => importRef.current?.click()}
            data-testid="wikidata-items-import-btn"
          >
            Import overrides
          </button>
          <input
            ref={importRef}
            type="file"
            accept=".json,.csv"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void handleImport(f);
              e.target.value = "";
            }}
          />
          <SectionExportMenu section="wikidata-studio" runId={runId} availableFormats={["json", "csv", "ttl"]} approvedOnly={approvedOnly} />
          <button type="button" className="button-ghost text-xs" onClick={() => void refresh()} disabled={loading}>
            Refresh
          </button>
          <button
            type="button"
            className="button-ghost text-xs"
            disabled={!filteredIds.length}
            data-testid="wikidata-items-verify-ai"
            onClick={() => openVerify(filteredIds, "audit_wikidata_item")}
          >
            Verify with AI ({filteredIds.length})
          </button>
          <button
            type="button"
            className="button-ghost text-xs"
            disabled={!autofixItemIds.length}
            data-testid="wikidata-items-autofix-ai"
            onClick={() => openVerify(autofixItemIds, "autofix_from_wikidata")}
          >
            Autofix with AI ({autofixItemIds.length})
          </button>
          <button
            type="button"
            className="button-primary text-xs"
            disabled={!pendingVisibleIds.length || approveBusy}
            data-testid="wikidata-items-approve-visible"
            title="Approve every currently filtered row that is not already approved (runs as a background job)."
            onClick={() => void approveAllVisible()}
          >
            {approveBusy
              ? "Approving…"
              : `Approve all visible (${pendingVisibleIds.length})`}
          </button>
        </div>
      </div>

      <div className="flex flex-wrap gap-2 items-center border-b border-white/5 pb-4" data-testid="wikidata-item-lifecycle-bar">
        <GlassPill as="div" className="px-1 py-1 flex gap-1 text-xs">
          <button
            type="button"
            onClick={() => onApprovedOnlyChange(false)}
            className={`px-3 py-1 rounded-full transition ${!approvedOnly ? "bg-white/12 text-ink" : "muted hover:text-ink"}`}
          >
            All matches
          </button>
          <button
            type="button"
            onClick={() => onApprovedOnlyChange(true)}
            className={`px-3 py-1 rounded-full transition ${approvedOnly ? "bg-white/12 text-ink" : "muted hover:text-ink"}`}
          >
            Approved only
          </button>
        </GlassPill>
        <button
          type="button"
          className="button-ghost text-sm"
          disabled={loading || (studioBuildJob != null && isJobActive(studioBuildJob.status))}
          onClick={() => void refresh({nextForceRebuild: forceRebuild})}
        >
          {loading || (studioBuildJob != null && isJobActive(studioBuildJob.status))
            ? "Rebuilding…"
            : "Rebuild"}
        </button>
        <label className="flex items-center gap-1.5 text-xs muted cursor-pointer select-none">
          <input
            type="checkbox"
            checked={forceRebuild}
            onChange={(e) => onForceRebuildChange(e.target.checked)}
            className="accent-biu-sky"
            data-testid="wikidata-rebuild-skip-cache"
          />
          Skip cache (force fresh build)
        </label>
        <label className="flex items-center gap-1.5 text-xs muted cursor-pointer select-none">
          <input
            type="checkbox"
            checked={uploadApprovedOnly}
            onChange={(e) => onUploadApprovedOnlyChange(e.target.checked)}
            className="accent-biu-sky"
            data-testid="wikidata-upload-approved-only"
          />
          Approved items only
        </label>
        {build && (
          <span className="text-[11px] muted">
            {build.approved_item_count} of {build.summary.total_items} approved
          </span>
        )}
        <a
          href={Studio.qsUrl(runId, approvedOnly, uploadApprovedOnly, true)}
          download
          className="button-ghost text-sm ml-auto"
          data-testid="wikidata-qs-download"
        >
          Download QuickStatements (gated)
        </a>
      </div>

      <WikidataUploadPanel
        runId={runId}
        source={source}
        approvedOnly={approvedOnly}
        uploadApprovedOnly={uploadApprovedOnly}
        buildPresent={buildPresent}
        refreshToken={refreshToken}
        compact
        uploadTarget={uploadTarget}
        onUploadTargetChange={setUploadTarget}
        onUploaded={(meta) => {
          setUploadTarget(meta.upload_target);
          setMoratoriumLifted(meta.moratorium_lifted);
          setTestMode(meta.test_mode);
          void loadItems({silent: true});
        }}
        onUploadOutcomes={applyUploadOutcomes}
      />

      {error && <p className="text-danger text-sm">{error}</p>}
      {approveFeedback && <p className="text-sm text-biu-sky" role="status">{approveFeedback}</p>}
      {studioBuildJob && (
        <JobProgressInline
          job={studioBuildJob}
          labels={{
            running: "Building Wikidata items…",
            succeeded: "Build complete:",
            failed: "Build failed:",
            cancelled: "Build cancelled:",
          }}
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
      {buildPresent && (Boolean(build?.items?.length) || !loading) && (
        <WikidataItemTable
          items={build?.items ?? []}
          onFilteredChange={setFilteredIds}
          onOpenItem={setOpenItem}
          onToggleApproved={(item, next) => void handleToggleApproved(item, next)}
        />
      )}
      {!buildPresent && !loading && (
        <p className="muted text-sm">
          {source === "canonical"
            ? "No HMO canonical records yet — complete the HMO upload read-back first."
            : "No items yet — rebuild after approving authority matches."}
        </p>
      )}

      {openItem && (
        <WikidataItemDetailDrawer
          runId={runId}
          projectId={projectId}
          item={openItem}
          source={source}
          approvedOnly={approvedOnly}
          moratoriumLifted={moratoriumLifted}
          testMode={testMode}
          uploadTarget={uploadTarget === "dry_run" ? "test" : uploadTarget}
          onClose={() => setOpenItem(null)}
          onSaved={() => void loadItems({silent: true})}
          onVerify={() => openVerify([openItem.local_id ?? ""], "audit_wikidata_item")}
          onAutofix={
            openItem.existing_qid?.trim()
              ? () => openVerify([openItem.local_id ?? ""], "autofix_from_wikidata")
              : undefined
          }
        />
      )}

      {verifyOpen && (
        <WikidataVerificationModal
          runId={runId}
          scopeKind={verifyIds?.length === 1 ? "single" : "selection"}
          itemIds={verifyIds}
          scopeLabel={verifyIds?.length === 1 ? `Item ${verifyIds[0]}` : `${verifyIds?.length ?? 0} items`}
          initialActionId={verifyActionId}
          source={source}
          approvedOnly={approvedOnly}
          onVerdictsLanded={() => void loadItems({silent: true})}
          onClose={() => {
            setVerifyOpen(false);
            setVerifyActionId(undefined);
          }}
        />
      )}
    </Glass>
  );
}
