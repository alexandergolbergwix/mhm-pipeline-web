import {useCallback, useEffect, useMemo, useRef, useState} from "react";

import {ApiError} from "@/api/client";
import {
  Studio,
  fetchAllStudioItems,
  type StudioBuild,
  type StudioItem,
} from "@/api/wikidataStudio";
import {SectionExportMenu} from "@/components/export/SectionExportMenu";
import {Glass, GlassPill} from "@/components/glass";
import {LoadingOverlay} from "@/components/LoadingOverlay";
import {WikidataItemDetailDrawer} from "@/components/wikidata/WikidataItemDetailDrawer";
import {WikidataItemTable} from "@/components/wikidata/WikidataItemTable";
import {WikidataUploadPanel} from "@/components/wikidata/WikidataUploadPanel";
import {WikidataVerificationModal} from "@/components/wikidata/WikidataVerificationModal";
import {loadStudioBuild, waitForStudioBuild} from "@/utils/waitForRunJob";
import {useLabelStore} from "@/api/wikidataLabels";

export interface WikidataItemsPanelProps {
  runId: string;
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
  const [moratoriumLifted, setMoratoriumLifted] = useState(false);
  const [testMode, setTestMode] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const importRef = useRef<HTMLInputElement>(null);
  const labelStore = useLabelStore();

  const autofixItemIds = useMemo(() => {
    const visible = new Set(filteredIds);
    return (build?.items ?? [])
      .filter((item) => item.local_id && visible.has(item.local_id) && Boolean(item.existing_qid?.trim()))
      .map((item) => item.local_id as string);
  }, [build?.items, filteredIds]);

  const refresh = useCallback(async (opts?: {nextForceRebuild?: boolean}) => {
    setLoading(true);
    setError(null);
    setBuildProgress(null);
    const force = opts?.nextForceRebuild ?? forceRebuild;
    try {
      if (force) {
        setBuildProgress("Starting fresh build…");
        await waitForStudioBuild(runId, {approvedOnly, forceRebuild: true});
      }
      const fetchPage = () => fetchAllStudioItems(runId, {
        approvedOnly,
        forceRebuild: false,
      });
      setBuildProgress(force ? "Loading items…" : "Checking cache…");
      const result = await loadStudioBuild(runId, fetchPage, {
        onProgress: (message) => { setBuildProgress(message); },
      }) as StudioBuild;
      setBuild(result);
      if (result.property_labels) labelStore.seed(result.property_labels);
      onBuildLoaded?.(result);
      setRefreshToken((t) => t + 1);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setLoading(false);
      setBuildProgress(null);
    }
  }, [approvedOnly, forceRebuild, labelStore, onBuildLoaded, runId]);

  useEffect(() => {
    void refresh();
  }, [runId, approvedOnly]); // eslint-disable-line react-hooks/exhaustive-deps

  const openVerify = useCallback((itemIds: string[], actionId?: string) => {
    setVerifyIds(itemIds);
    setVerifyActionId(actionId);
    setVerifyOpen(true);
  }, []);

  const handleToggleApproved = useCallback(async (item: StudioItem, next: boolean) => {
    if (!item.local_id) return;
    await Studio.patchItemOverride(runId, item.local_id, {approved: next});
    await refresh();
  }, [refresh, runId]);

  const approveAllVisible = useCallback(async () => {
    if (!filteredIds.length) return;
    const ok = window.confirm(`Approve all ${filteredIds.length} visible items?`);
    if (!ok) return;
    for (const id of filteredIds) {
      await Studio.patchItemOverride(runId, id, {approved: true});
    }
    await refresh();
  }, [filteredIds, refresh, runId]);

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
            Review table for this run&apos;s Wikidata build. Open a row for overrides, compare, reconcile,
            AI verify, and single-item push.
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
            className="button-ghost text-xs"
            disabled={!filteredIds.length}
            data-testid="wikidata-items-approve-visible"
            onClick={() => void approveAllVisible()}
          >
            Approve all visible ({filteredIds.length})
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
          disabled={loading}
          onClick={() => void refresh({nextForceRebuild: forceRebuild})}
        >
          {loading ? "Rebuilding…" : "Rebuild"}
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
        approvedOnly={approvedOnly}
        uploadApprovedOnly={uploadApprovedOnly}
        buildPresent={buildPresent}
        refreshToken={refreshToken}
        compact
        onUploaded={(meta) => {
          setMoratoriumLifted(meta.moratorium_lifted);
          setTestMode(meta.test_mode);
          void refresh();
        }}
      />

      {error && <p className="text-danger text-sm">{error}</p>}
      {buildPresent && !loading && (
        <WikidataItemTable
          items={build?.items ?? []}
          onFilteredChange={setFilteredIds}
          onOpenItem={setOpenItem}
          onToggleApproved={(item, next) => void handleToggleApproved(item, next)}
        />
      )}
      {!buildPresent && !loading && (
        <p className="muted text-sm">No items yet — rebuild after approving authority matches.</p>
      )}

      {openItem && (
        <WikidataItemDetailDrawer
          runId={runId}
          projectId={projectId}
          item={openItem}
          approvedOnly={approvedOnly}
          moratoriumLifted={moratoriumLifted}
          testMode={testMode}
          onClose={() => setOpenItem(null)}
          onSaved={() => void refresh()}
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
          onVerdictsLanded={() => void refresh()}
          onClose={() => {
            setVerifyOpen(false);
            setVerifyActionId(undefined);
          }}
        />
      )}
    </Glass>
  );
}
