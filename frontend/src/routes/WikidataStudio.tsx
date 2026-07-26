import {useCallback, useEffect, useMemo, useState} from "react";
import {Link, useParams} from "react-router-dom";

import {Layout} from "@/components/Layout";
import {ApiError} from "@/api/client";
import {MarcRecordPopup} from "@/components/MarcRecordPopup";
import {HistoryTimeline} from "@/components/history/HistoryTimeline";
import {useProjectEvents} from "@/api/realtime";
import {Runs} from "@/api/runs";
import {RunJobs} from "@/api/runJobs";
import {useRunJobs} from "@/stores/runJobs";
import {loadStudioBuild, waitForRunJob, waitForStudioBuild} from "@/utils/waitForRunJob";
import {useLabelStore} from "@/api/wikidataLabels";
import {useDebounce} from "@/hooks/useDebounce";
import {
  collectIds, FRIENDLY_S_PROP, PropertyPill, ValueRendering,
  type LabelStore,
} from "@/components/StatementCells";
import {ItemOverrideDialog} from "@/components/ItemOverrideDialog";
import {SectionExportMenu} from "@/components/export/SectionExportMenu";
import {SectionImportButton} from "@/components/import/SectionImportButton";
import {WikidataItemsPanel} from "@/components/wikidata/WikidataItemsPanel";
import {ItemValidatorBadge} from "@/components/wikidata/ItemValidatorBadge";
import {ItemApprovalBadge} from "@/components/wikidata/ItemApprovalBadge";
import {WikidataComparePanel} from "@/components/wikidata/WikidataComparePanel";
import {WikidataVerificationModal} from "@/components/wikidata/WikidataVerificationModal";
import {
  Studio,
  type PropertyInfo,
  type ReconcileOutcome,
  type Snak,
  type StudioBuild,
  type StudioItem,
  type UploadOutcome,
} from "@/api/wikidataStudio";
import {Glass, GlassPill} from "@/components/glass";
import {CuratorTableScroll} from "@/components/CuratorTableScroll";
import {LoadingOverlay, LoadingPanel} from "@/components/LoadingOverlay";

type EntityFilter = "all" | "manuscript" | "person" | "work";
type ExistFilter  = "any" | "existing" | "new";
type SortKey      = "label" | "statements" | "entity_type" | "wikidata";
type ReviewMode = "modern" | "legacy";
type View         = "item" | "table";

// Studio table-view sort
type StmtSortKey  = "item" | "type" | "property" | "value" | "rank";
type SortDir      = "asc" | "desc";


export default function WikidataStudio() {
  const { runId } = useParams<{ runId: string }>();
  const [build, setBuild] = useState<StudioBuild | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [approvedOnly, setApprovedOnly] = useState(true);
  const [projectionSource, setProjectionSource] = useState<"legacy" | "canonical">("canonical");

  // Project id is needed for the per-item-card "📜 View edit history"
  // affordance (HistoryTimeline is keyed by (project, entity_type,
  // entity_id)). Fetched once on mount; empty until resolved, in
  // which case the history button is hidden.
  const [projectId, setProjectId] = useState<string>("");
  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    Runs.get(runId)
      .then((r) => { if (!cancelled) setProjectId(r.project_id); })
      .catch(() => { /* non-fatal: history button stays hidden */ });
    return () => { cancelled = true; };
  }, [runId]);

  useProjectEvents(projectId || undefined, (msg) => {
    if (msg.type === "run_job_update" && msg.job) {
      useRunJobs.getState().upsertJob(msg.job);
    }
  });

  const [historyFor, setHistoryFor] = useState<{ id: string } | null>(null);
  const [editItem, setEditItem] = useState<StudioItem | null>(null);
  const [compareTarget, setCompareTarget] = useState<{item: StudioItem; qid: string} | null>(null);
  const [verifyScope, setVerifyScope] = useState<
    null | { scopeKind: "single" | "all"; itemIds: string[]; label: string }
  >(null);

  // search + filter + sort state (drive the server query)
  const [query, setQuery]               = useState("");
  const debouncedQuery                  = useDebounce(query, 300);
  const [entityFilter, setEntityFilter] = useState<EntityFilter>("all");
  const [existFilter, setExistFilter]   = useState<ExistFilter>("any");
  const [minStmts, setMinStmts]         = useState<number>(0);
  const [sortKey, setSortKey]           = useState<SortKey>("label");
  const [sortDesc, setSortDesc]         = useState(false);
  const [page, setPage]                 = useState(1);
  const PAGE_SIZE                       = 50;

  const [selectedIdx, setSelectedIdx] = useState(0);

  // reconcile/upload state
  const [reconcileMap, setReconcileMap] = useState<Record<string, ReconcileOutcome>>({});
  const [uploadMap, setUploadMap]       = useState<Record<string, UploadOutcome>>({});
  const [busy, setBusy] = useState<"reconcile" | "dry" | "live" | null>(null);
  const [lastUpload, setLastUpload] = useState<{
    dry_run: boolean; moratorium_lifted: boolean; test_mode: boolean;
  } | null>(null);
  const [forceRebuild, setForceRebuild] = useState(false);
  const [cacheStale, setCacheStale] = useState(false);
  const [uploadApprovedOnly, setUploadApprovedOnly] = useState(false);
  const [buildProgress, setBuildProgress] = useState<string | null>(null);

  // approval toggle loading — keyed by local_id
  const [approvalLoading, setApprovalLoading] = useState<Record<string, boolean>>({});

  const [marcPopupCn, setMarcPopupCn] = useState<string | null>(null);
  const labelStore = useLabelStore();

  // Modern table+drawer (default) vs legacy sidebar Item/Table views.
  const [reviewMode, setReviewMode] = useState<ReviewMode>(() =>
    (localStorage.getItem("mhm.studio.reviewMode") as ReviewMode) || "modern");
  useEffect(() => { localStorage.setItem("mhm.studio.reviewMode", reviewMode); }, [reviewMode]);

  // Item view (one item at a time) vs. table view (every statement in
  // the run, flat). Persisted across reloads.
  const [view, setView] = useState<View>(() =>
    (localStorage.getItem("mhm.studio.view") as View) || "item");
  useEffect(() => { localStorage.setItem("mhm.studio.view", view); }, [view]);

  async function refresh(opts?: {
    nextApprovedOnly?: boolean;
    nextForceRebuild?: boolean;
    nextPage?: number;
  }) {
    if (!runId) return;
    const flag  = opts?.nextApprovedOnly ?? approvedOnly;
    const force = opts?.nextForceRebuild ?? forceRebuild;
    const pg    = opts?.nextPage ?? page;
    setLoading(true); setError(null); setBuildProgress(null);
    try {
      if (force) {
        setBuildProgress("Starting fresh build…");
        await waitForStudioBuild(runId, {approvedOnly: flag, forceRebuild: true, source: projectionSource});
      }
      const fetchPage = () => Studio.build(runId, {
        source: projectionSource,
        approvedOnly: flag,
        forceRebuild: false,
        entityType: entityFilter !== "all" ? entityFilter : null,
        q: debouncedQuery || null,
        sort: sortKey,
        sortDir: sortDesc ? "desc" : "asc",
        page: pg,
        pageSize: PAGE_SIZE,
      });
      setBuildProgress(force ? "Loading items…" : "Checking cache…");
      let result = await loadStudioBuild(runId, fetchPage, {
        onProgress: (message) => { setBuildProgress(message); },
      }) as StudioBuild;
      setBuild(result);
      setCacheStale(Boolean(result.cache_stale));
      if (result.property_labels) {
        labelStore.seed(result.property_labels);
      }
    }
    catch (e) { setError(e instanceof ApiError ? e.detail : String(e)); }
    finally   { setLoading(false); setBuildProgress(null); }
  }

  // Filter/sort changes reset pagination; page-1 loads happen here.
  useEffect(() => {
    setPage(1);
    void refresh({nextPage: 1});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, approvedOnly, projectionSource, entityFilter, debouncedQuery, sortKey, sortDesc]);

  // Page > 1 only — page 1 is covered by the effect above (avoids double fetch on mount).
  useEffect(() => {
    if (page === 1) return;
    void refresh();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  async function reconcile() {
    if (!runId) return;
    setBusy("reconcile"); setError(null);
    try {
      const r = await Studio.reconcile(runId, approvedOnly, projectionSource);
      const map: Record<string, ReconcileOutcome> = {};
      r.outcomes.forEach((o, i) => { map[`${o.entity_type}:${i}`] = o; });
      setReconcileMap(map);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally { setBusy(null); }
  }

  async function doUpload(dry: boolean) {
    if (!runId) return;
    setBusy(dry ? "dry" : "live"); setError(null);
    try {
      const job = await RunJobs.start(runId, "wikidata_upload", {
        dry_run: dry,
        approved_only: approvedOnly,
        source: projectionSource,
        item_approved_only: uploadApprovedOnly,
      }).catch(async (e) => {
        if (e instanceof ApiError && e.status === 409) {
          const {jobs} = await RunJobs.listForRun(runId, true);
          const active = jobs.find((j) => j.kind === "wikidata_upload");
          if (active) return active;
        }
        throw e;
      });
      const finished = await waitForRunJob(runId, job.id);
      if (finished.status === "succeeded" && finished.result) {
        const outcomes = (finished.result.outcomes as UploadOutcome[]) ?? [];
        const map: Record<string, UploadOutcome> = {};
        outcomes.forEach((o, i) => { map[`${o.entity_type}:${i}`] = o; });
        setUploadMap(map);
        setLastUpload({
          dry_run: Boolean(finished.result.dry_run),
          moratorium_lifted: Boolean(finished.result.moratorium_lifted),
          test_mode: Boolean(finished.result.test_mode),
        });
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally { setBusy(null); }
  }

  const toggleApproval = useCallback(async (item: StudioItem) => {
    if (!runId || !item.local_id) return;
    const id = item.local_id;
    const nextApproved = !item.approved;
    setApprovalLoading((prev) => ({...prev, [id]: true}));
    try {
      await Studio.patchItemOverride(runId, id, {approved: nextApproved});
      // Optimistically update the page + approved_item_count
      setBuild((prev) => {
        if (!prev) return prev;
        const delta = nextApproved ? 1 : -1;
        return {
          ...prev,
          items: prev.items.map((it) =>
            it.local_id === id ? {...it, approved: nextApproved} : it,
          ),
          approved_item_count: Math.max(0, prev.approved_item_count + delta),
        };
      });
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setApprovalLoading((prev) => ({...prev, [id]: false}));
    }
  }, [runId]);

  // ── item rows — server already filtered + sorted + paginated ───────────
  // Client applies only the local-only filters (existFilter, minStmts) that
  // are cheap and don't warrant a round-trip.
  const itemRows = useMemo(() => {
    if (!build) return [] as Array<{ it: StudioItem; idx: number; key: string }>;
    return build.items
      .map((it, idx) => ({it, idx, key: `${it.entity_type ?? "other"}:${idx}`}))
      .filter(({ it }) => {
        if (existFilter === "existing" && !it.existing_qid) return false;
        if (existFilter === "new" && it.existing_qid) return false;
        if ((it.statements ?? []).length < minStmts) return false;
        return true;
      });
  }, [build, existFilter, minStmts]);

  if (error) return <Layout><Glass className="p-6 text-danger">{error}</Glass></Layout>;

  if (reviewMode === "modern" && runId) {
    return (
      <Layout>
        <div className="space-y-6">
          <Glass as="section" className="p-6 space-y-2">
            <div className="kicker">
              Wikidata Studio · <Link to={`/runs/${runId}/overview`} className="hover:text-ink underline">back to run</Link>
            </div>
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <div>
                <h2 className="text-2xl font-semibold">Review records for Wikidata</h2>
                <p className="muted text-sm mt-1">Prepared from the reviewed HMO Wikibase records. Nothing is published until you choose a public upload.</p>
              </div>
              <GlassPill as="div" className="px-1 py-1 flex gap-1 text-xs">
              <button type="button" onClick={() => { setProjectionSource("legacy"); }} className={projectionSource === "legacy" ? "px-3 py-1 rounded-full bg-white/12 text-ink" : "px-3 py-1 rounded-full muted"}>Legacy</button>
              <button type="button" onClick={() => { setProjectionSource("canonical"); }} className={projectionSource === "canonical" ? "px-3 py-1 rounded-full bg-biu-sky/20 text-ink" : "px-3 py-1 rounded-full muted"}>Reviewed HMO records</button>
                <button
                  type="button"
                  onClick={() => setReviewMode("modern")}
                  className="px-3 py-1 rounded-full transition bg-white/12 text-ink"
                >
                  Review table
                </button>
                <button
                  type="button"
                  onClick={() => setReviewMode("legacy")}
                  className="px-3 py-1 rounded-full transition muted hover:text-ink"
                >
                  Legacy sidebar
                </button>
              </GlassPill>
            </div>
          </Glass>

          <WikidataItemsPanel
            runId={runId}
            source={projectionSource}
            projectId={projectId}
            approvedOnly={approvedOnly}
            uploadApprovedOnly={uploadApprovedOnly}
            forceRebuild={forceRebuild}
            onApprovedOnlyChange={setApprovedOnly}
            onForceRebuildChange={setForceRebuild}
            onUploadApprovedOnlyChange={setUploadApprovedOnly}
            onBuildLoaded={setBuild}
          />
        </div>
      </Layout>
    );
  }

  if (!build) return (
    <Layout>
      <Glass className="p-6">
        <LoadingPanel
          title={loading ? "Building Wikidata items…" : "Loading Wikidata Studio…"}
          detail={
            buildProgress
            ?? (loading
              ? "Large runs can take a few minutes on first load while items are built and cached. Check the job tray (bottom-right) for progress."
              : null)
          }
        />
      </Glass>
    </Layout>
  );

  const current = itemRows.length > 0 ? itemRows[Math.min(selectedIdx, itemRows.length - 1)].it : null;
  const currentKey = itemRows.length > 0 ? itemRows[Math.min(selectedIdx, itemRows.length - 1)].key : "";
  const visibleItemIds = itemRows.map(({ it }) => it.local_id).filter((id): id is string => Boolean(id));

  return (
    <Layout>
      <div className="space-y-6">
        <Glass as="section" className="p-6 space-y-2">
          <div className="kicker">
            Wikidata Studio · <Link to={`/runs/${runId}/overview`} className="hover:text-ink underline">back to run</Link>
          </div>
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 className="text-2xl font-semibold">Items ready to upload</h2>
            <GlassPill as="div" className="px-1 py-1 flex gap-1 text-xs">
              <button
                type="button"
                onClick={() => setReviewMode("modern")}
                className={`px-3 py-1 rounded-full transition ${reviewMode === "modern" ? "bg-white/12 text-ink" : "muted hover:text-ink"}`}
              >
                Review table
              </button>
              <button
                type="button"
                onClick={() => setReviewMode("legacy")}
                className={`px-3 py-1 rounded-full transition ${reviewMode === "legacy" ? "bg-white/12 text-ink" : "muted hover:text-ink"}`}
              >
                Legacy sidebar
              </button>
            </GlassPill>
          </div>
          <p className="muted text-sm leading-relaxed max-w-2xl w-full">
            Every candidate match feeds the builder by default. Flip
            <b className="text-ink"> Approved only</b> before exporting
            the final QuickStatements. <b className="text-ink">Reconcile</b>{" "}
            SPARQL-queries Wikidata so duplicates show up before upload;
            <b className="text-ink"> Upload</b> writes through the same{" "}
            four guards that protect against modifying items you didn't
            create.
          </p>

          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 pt-2">
            <div className="col-span-full text-xs uppercase tracking-[0.18em] text-biu-sky">Source: {build.source === "canonical" ? "HMO Wikibase canonical entities" : "legacy MARC enrichment"}</div>
            <Stat label="Records" value={build.record_count} />
            <Stat label="Matches fed" value={build.used_match_count}
                  sub={`${build.approved_match_count} approved · ${build.pending_match_count} pending`} />
            <Stat label="Items" value={build.summary.total_items} highlight />
            <Stat label="Statements" value={build.summary.statements} />
            <Stat label="MS / P / W"
                  value={`${build.summary.manuscripts} / ${build.summary.persons} / ${build.summary.works}`} />
          </div>

          {cacheStale && !loading && (
            <GlassPill className="px-3 py-2 text-xs flex flex-wrap items-center gap-2 text-warn">
              <span>
                Showing cached items that may not reflect your latest approval changes.
              </span>
              <button
                type="button"
                className="button-ghost !py-0.5 !px-2 text-xs"
                disabled={!!busy}
                onClick={() => { void refresh({nextForceRebuild: true}); }}
              >
                Rebuild now
              </button>
            </GlassPill>
          )}

          {/* Action row */}
          <div className="flex flex-wrap gap-2 pt-2 items-center">
            <GlassPill as="div" className="px-1 py-1 flex gap-1 text-xs">
              <button
                onClick={() => { setApprovedOnly(false); }}
                className={`px-3 py-1 rounded-full transition ${
                  !approvedOnly ? "bg-white/12 text-ink" : "muted hover:text-ink"
                }`}>All matches</button>
              <button
                onClick={() => { setApprovedOnly(true); }}
                className={`px-3 py-1 rounded-full transition ${
                  approvedOnly ? "bg-white/12 text-ink" : "muted hover:text-ink"
                }`}>Approved only</button>
            </GlassPill>
            <button onClick={() => refresh({nextForceRebuild: forceRebuild})} disabled={loading || !!busy} className="button-ghost text-sm">
              {loading ? "Rebuilding…" : "Rebuild"}
            </button>
            <label className="flex items-center gap-1.5 text-xs muted cursor-pointer select-none">
              <input
                type="checkbox"
                checked={forceRebuild}
                onChange={(e) => setForceRebuild(e.target.checked)}
                className="accent-biu-sky"
              />
              Skip cache (force fresh build)
            </label>
            <button onClick={reconcile} disabled={!!busy} className="button-ghost text-sm">
              {busy === "reconcile" ? "Reconciling…" : "Reconcile with Wikidata"}
            </button>
            <button
              onClick={() => {
                if (!current?.local_id) return;
                setVerifyScope({
                  scopeKind: "single",
                  itemIds: [current.local_id],
                  label: labelOf(current) || current.local_id,
                });
              }}
              disabled={!current?.local_id}
              className="button-ghost text-sm text-biu-sky"
            >
              ✨ Verify current with AI
            </button>
            <button
              onClick={() => setVerifyScope({
                scopeKind: "all",
                itemIds: visibleItemIds,
                label: `${visibleItemIds.length} visible`,
              })}
              disabled={visibleItemIds.length === 0}
              className="button-ghost text-sm text-biu-sky"
            >
              ✨ Verify all visible with AI
            </button>
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1.5 text-xs muted cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={uploadApprovedOnly}
                  onChange={(e) => setUploadApprovedOnly(e.target.checked)}
                  className="accent-biu-sky"
                />
                Approved items only
              </label>
              {build && build.summary.total_items > 0 && (
                <span className="text-[11px] muted">
                  {build.approved_item_count} of {build.summary.total_items} approved
                </span>
              )}
            </div>
            <button onClick={() => doUpload(true)} disabled={!!busy} className="button-primary text-sm">
              {busy === "dry" ? "Running…" : "Dry-run upload"}
            </button>
            <button onClick={() => doUpload(false)} disabled={!!busy} className="button-ghost text-sm text-warn">
              {busy === "live" ? "Uploading…" : "Live upload"}
            </button>
            <a href={Studio.qsUrl(runId!, approvedOnly, uploadApprovedOnly, true, projectionSource)} download className="button-ghost text-sm">
              Download QuickStatements.txt
            </a>
            {runId && (
              <SectionExportMenu
                section="wikidata-studio"
                runId={runId}
                availableFormats={["json", "csv", "ttl"]}
                approvedOnly={approvedOnly}
              />
            )}
            {runId && (
              <SectionImportButton
                section="wikidata-studio"
                runId={runId}
                onComplete={() => { void refresh(); }}
              />
            )}

            {/* View-mode toggle — Item (focus on one item's statements)
                vs. Table (every statement across every item, flat). */}
            <GlassPill as="div" className="px-1 py-1 flex gap-1 text-xs ml-auto">
              <button
                onClick={() => setView("item")}
                className={`px-3 py-1 rounded-full transition ${
                  view === "item" ? "bg-white/12 text-ink" : "muted hover:text-ink"
                }`}>Item view</button>
              <button
                onClick={() => setView("table")}
                className={`px-3 py-1 rounded-full transition ${
                  view === "table" ? "bg-white/12 text-ink" : "muted hover:text-ink"
                }`}>Table view</button>
            </GlassPill>
          </div>

          {lastUpload && (
            <p className="text-xs muted pt-2">
              Last upload: {lastUpload.dry_run ? "dry-run" : "live"}
              {!lastUpload.dry_run && (
                <> · moratorium {lastUpload.moratorium_lifted ? "lifted" : "in effect"}
                  {lastUpload.test_mode && " · test.wikidata.org"}</>
              )}
            </p>
          )}
        </Glass>

        {build.summary.total_items === 0 ? (
          <Glass as="section" className="p-6 text-center muted">
            {build.used_match_count === 0 && approvedOnly
              ? <>No approved matches yet. Either click <b className="text-ink">All matches</b> above
                  to preview, or approve rows on the{" "}
                  <Link to={`/runs/${runId}/hmo-studio`} className="text-biu-sky hover:underline">HMO Wikibase Studio</Link> page.</>
              : <>No items yet. Upload a MARC file via the project page first.</>}
          </Glass>
        ) : (
          <div className="relative">
            {loading && (
              <LoadingOverlay
                message="Updating Wikidata items…"
                detail={buildProgress}
                className="rounded-xl z-30"
              />
            )}
        {view === "table" ? (
          <StatementTableView
            items={itemRows.map(({ it, idx }) => ({ it, idx }))}
            properties={build.properties}
            labelStore={labelStore}
            onOpenItem={(idx) => {
              setView("item");
              // Position selectedIdx so that itemRows[selectedIdx] is the
              // clicked item (if it survives the current filter).
              const pos = itemRows.findIndex(({ idx: i }) => i === idx);
              if (pos >= 0) setSelectedIdx(pos);
            }}
            onOpenMarc={setMarcPopupCn} />
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4">

            {/* Left: advanced search + filter + sort + list */}
            <Glass as="aside" className="p-3 max-h-[78vh] overflow-auto space-y-3">
              <input
                value={query} onChange={(e) => setQuery(e.target.value)}
                placeholder="Search…"
                className="input-glass !py-1.5 text-sm" />

              <div className="grid grid-cols-2 gap-2 text-xs">
                {(["all", "manuscript", "person", "work"] as EntityFilter[]).map((f) => (
                  <Chip key={f} active={entityFilter === f} onClick={() => setEntityFilter(f)}>
                    {f}
                  </Chip>
                ))}
              </div>

              <div className="text-xs space-y-1.5">
                <div className="flex items-center gap-2">
                  <label className="muted text-[11px] w-20 shrink-0">On Wikidata</label>
                  <select className="input-glass !py-1 text-xs flex-1"
                          value={existFilter}
                          onChange={(e) => setExistFilter(e.target.value as ExistFilter)}>
                    <option value="any">any</option>
                    <option value="existing">already exists</option>
                    <option value="new">not yet</option>
                  </select>
                </div>
                <div className="flex items-center gap-2">
                  <label className="muted text-[11px] w-20 shrink-0">Min stmts</label>
                  <input type="number" min={0} value={minStmts}
                         onChange={(e) => setMinStmts(Math.max(0, parseInt(e.target.value) || 0))}
                         className="input-glass !py-1 text-xs flex-1" />
                </div>
                <div className="flex items-center gap-2">
                  <label className="muted text-[11px] w-20 shrink-0">Sort</label>
                  <select className="input-glass !py-1 text-xs flex-1"
                          value={sortKey}
                          onChange={(e) => setSortKey(e.target.value as SortKey)}>
                    <option value="label">label</option>
                    <option value="statements"># statements</option>
                    <option value="entity_type">entity type</option>
                    <option value="wikidata">existing QID</option>
                  </select>
                  <button onClick={() => setSortDesc((v) => !v)}
                          title={sortDesc ? "descending" : "ascending"}
                          className="button-ghost !py-0.5 !px-2 text-xs">
                    {sortDesc ? "↓" : "↑"}
                  </button>
                </div>
              </div>

              <div className="border-t border-white/5 pt-2 text-[10px] muted uppercase tracking-wider flex items-center justify-between">
                <span>{build.total} items</span>
                {build.total > PAGE_SIZE && (
                  <span>p.{page}/{Math.ceil(build.total / PAGE_SIZE)}</span>
                )}
              </div>

              <ul>
                {itemRows.map(({ it, key }, listIdx) => {
                  const rec = reconcileMap[key];
                  const up  = uploadMap[key];
                  const issues = it.validation_issues ?? [];
                  return (
                    <li key={key}>
                      <button onClick={() => setSelectedIdx(listIdx)}
                              className={`block w-full text-left px-2 py-2 rounded-lg transition ${
                                listIdx === selectedIdx
                                  ? "bg-white/8 text-ink"
                                  : "muted hover:text-ink hover:bg-white/5"
                              }`}>
                        <div className="flex justify-between gap-2 items-center">
                          <span className="text-sm truncate">
                            {labelOf(it) || "(no label)"}
                          </span>
                          <div className="flex items-center gap-1.5 shrink-0">
                            <ItemApprovalBadge
                              approved={it.approved}
                              loading={approvalLoading[it.local_id ?? ""] ?? false}
                              onToggle={() => toggleApproval(it)}
                            />
                            {issues.length > 0 && (
                              <ItemValidatorBadge issues={issues} />
                            )}
                            <span className="kicker">{it.entity_type ?? "?"}</span>
                          </div>
                        </div>
                        <div className="flex items-center gap-1 flex-wrap mt-0.5 text-[10px]">
                          <span className="muted">{(it.statements ?? []).length} stmts</span>
                          {it.existing_qid && (
                            <span className="text-biu-sky font-mono">↻{it.existing_qid}</span>
                          )}
                          {rec?.existing_qid && !it.existing_qid && (
                            <span className="text-warn font-mono" title={rec.message}>
                              found {rec.existing_qid}
                            </span>
                          )}
                          {up && (
                            <span className={statusTone(up.status)} title={up.message}>
                              {up.status}
                            </span>
                          )}
                        </div>
                      </button>
                    </li>
                  );
                })}
                {itemRows.length === 0 && !loading && (
                  <p className="muted text-sm italic px-2">Nothing matches your filter.</p>
                )}
              </ul>
              {build.total > PAGE_SIZE && (
                <div className="flex items-center justify-between pt-2 border-t border-white/5 text-xs">
                  <button
                    onClick={() => { const p = Math.max(1, page - 1); setPage(p); }}
                    disabled={page <= 1 || loading}
                    className="button-ghost !py-0.5 !px-2 text-xs disabled:opacity-40"
                  >← Prev</button>
                  <span className="muted">
                    {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, build.total)} of {build.total}
                  </span>
                  <button
                    onClick={() => { const p = page + 1; setPage(p); }}
                    disabled={page * PAGE_SIZE >= build.total || loading}
                    className="button-ghost !py-0.5 !px-2 text-xs disabled:opacity-40"
                  >Next →</button>
                </div>
              )}
            </Glass>

            {/* Right: detail */}
            <main>
              {current && (
                <ItemPanel
                  key={current.local_id ?? currentKey}
                  item={current}
                  reconcile={reconcileMap[currentKey]}
                  upload={uploadMap[currentKey]}
                  onOpenMarc={setMarcPopupCn}
                  onEdit={() => setEditItem(current)}
                  onCompareWikidata={(qid) => { setCompareTarget({item: current, qid}); }}
                  onOpenHistory={
                    projectId
                      ? (id) => setHistoryFor({id})
                      : undefined
                  }
                  onToggleApproval={() => toggleApproval(current)}
                  approvalLoading={approvalLoading[current.local_id ?? ""] ?? false}
                  onSaveExcluded={async (indices: number[]) => {
                    if (!runId || !current.local_id) return;
                    await Studio.patchItemOverride(runId, current.local_id, {remove_statements: indices});
                    void refresh({nextForceRebuild: true});
                  }}
                  labelStore={labelStore} />
              )}
            </main>
          </div>
        )}
          </div>
        )}

        <Glass as="details" className="p-6">
          <summary className="cursor-pointer kicker">
            QuickStatements output ({build.quickstatements.length.toLocaleString()} chars)
          </summary>
          <pre className="text-[11px] font-mono whitespace-pre-wrap mt-3 code-surface p-2.5"
               style={{maxHeight: "60vh", overflow: "auto"}}>
            {build.quickstatements || "(empty)"}
          </pre>
        </Glass>
      </div>

      {marcPopupCn && runId && (
        <MarcRecordPopup runId={runId} controlNumber={marcPopupCn}
                         onClose={() => setMarcPopupCn(null)} />
      )}
      {editItem && runId && (
        <ItemOverrideDialog
          runId={runId}
          item={editItem}
          onClose={() => setEditItem(null)}
          onSaved={() => { setEditItem(null); void refresh(); }}
        />
      )}
      {compareTarget && runId && (
        <WikidataComparePanel
          runId={runId}
          item={compareTarget.item}
          qid={compareTarget.qid}
          approvedOnly={approvedOnly}
          onClose={() => setCompareTarget(null)}
          onApplied={() => { void refresh({nextForceRebuild: true}); }}
        />
      )}
      {verifyScope && runId && (
        <WikidataVerificationModal
          runId={runId}
          scopeKind={verifyScope.scopeKind}
          itemIds={verifyScope.itemIds}
          scopeLabel={verifyScope.label}
          source={projectionSource}
          approvedOnly={approvedOnly}
          onClose={() => setVerifyScope(null)}
          onVerdictsLanded={() => { void refresh({nextForceRebuild: true}); }}
        />
      )}
      {historyFor && projectId ? (
        <Glass as="aside" variant="drawer" className="fixed right-0 top-0 h-full w-[460px] shadow-2xl z-50 overflow-auto" data-testid="wikidata-history-drawer">
          <HistoryTimeline
            projectId={projectId}
            entityType="wikidata_override"
            entityId={historyFor.id}
            onClose={() => setHistoryFor(null)}
          />
        </Glass>
      ) : null}
    </Layout>
  );
}


// ── components ──────────────────────────────────────────────────────────


function ItemPanel({
  item, reconcile, upload, onOpenMarc, onEdit, onCompareWikidata, onOpenHistory, labelStore,
  onToggleApproval, approvalLoading, onSaveExcluded,
}: {
  item: StudioItem;
  reconcile?: ReconcileOutcome;
  upload?: UploadOutcome;
  onOpenMarc: (cn: string) => void;
  onEdit?: () => void;
  onCompareWikidata?: (qid: string) => void;
  onOpenHistory?: (entityId: string) => void;
  labelStore: LabelStore;
  onToggleApproval?: () => void;
  approvalLoading?: boolean;
  onSaveExcluded?: (indices: number[]) => Promise<void>;
}) {
  const historyId = item.local_id ?? item.existing_qid ?? null;
  const labels = item.labels ?? {};
  const descriptions = item.descriptions ?? {};
  const aliases = item.aliases ?? {};
  const statements = item.statements ?? [];
  const issues = item.validation_issues ?? [];
  const wikidataQid = item.existing_qid || reconcile?.existing_qid || null;

  // Optimistic excluded-statement indices (resets when item changes via key= on caller)
  const [excluded, setExcluded] = useState<Set<number>>(new Set());
  const [excludeSaving, setExcludeSaving] = useState(false);

  async function toggleExclude(idx: number) {
    const next = new Set(excluded);
    if (next.has(idx)) next.delete(idx); else next.add(idx);
    setExcluded(next);
    if (onSaveExcluded) {
      setExcludeSaving(true);
      try { await onSaveExcluded(Array.from(next)); }
      finally { setExcludeSaving(false); }
    }
  }

  // Lazy-resolve every P/Q id appearing in this item's snaks against
  // live Wikidata, so PropertyPill / value rendering can show English
  // labels even for ids the desktop's static dictionary doesn't carry.
  useEffect(() => {
    const ids: string[] = [];
    statements.forEach((s) => collectIds(s, ids));
    labelStore.resolve(ids);
  }, [item, statements, labelStore]);

  return (
    <Glass className="p-6 space-y-5 max-h-[78vh] overflow-auto">
      <header className="space-y-1">
        <div className="kicker">{item.entity_type ?? "item"}</div>
        <div className="flex items-baseline justify-between gap-3">
          <h3 className="text-xl font-semibold">{labelOf(item) || "(no label)"}</h3>
          <div className="flex gap-1 shrink-0 items-center">
            {onToggleApproval && (
              <ItemApprovalBadge
                approved={item.approved}
                onToggle={onToggleApproval}
                loading={approvalLoading ?? false}
                expanded
              />
            )}
            {onEdit ? (
              <button type="button" onClick={onEdit}
                      data-testid="studio-item-edit"
                      className="button-ghost h-7 px-2 text-xs">
                Edit
              </button>
            ) : null}
            {onOpenHistory && historyId ? (
              <button
                type="button"
                data-testid={`history-button-${historyId}`}
                onClick={() => onOpenHistory(historyId)}
                aria-label="View edit history"
                title="View edit history"
                className="button-ghost h-7 px-2 text-xs"
              >📜</button>
            ) : null}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          {item.existing_qid && (
            <a className="text-biu-sky font-mono hover:underline"
               href={`https://www.wikidata.org/wiki/${item.existing_qid}`}
               target="_blank" rel="noreferrer">
              ↻ updates {item.existing_qid}
            </a>
          )}
          {wikidataQid && onCompareWikidata && (
            <button
              type="button"
              className="button-ghost text-xs !py-0.5"
              data-testid="studio-compare-wikidata"
              onClick={() => { onCompareWikidata(wikidataQid); }}
            >
              Compare with Wikidata
            </button>
          )}
          {reconcile && (
            <GlassPill className="px-2 py-0.5 text-xs">
              reconcile: {reconcile.existing_qid
                ? <>found <span className="font-mono text-biu-sky">{reconcile.existing_qid}</span> via {reconcile.method}</>
                : <span className="muted">no Wikidata match</span>}
            </GlassPill>
          )}
          {upload && (
            <GlassPill className={`px-2 py-0.5 text-xs ${statusTone(upload.status)}`} title={upload.message}>
              upload: {upload.status}
              {upload.qid && <span className="font-mono ml-1">{upload.qid}</span>}
            </GlassPill>
          )}
        </div>
        {issues.length > 0 && (
          <ItemValidatorBadge issues={issues} expanded />
        )}
      </header>

      {Object.keys(labels).length > 0 && (
        <Section title="Labels"><KvList items={labels} /></Section>
      )}
      {Object.keys(descriptions).length > 0 && (
        <Section title="Descriptions"><KvList items={descriptions} /></Section>
      )}
      {Object.keys(aliases).length > 0 && (
        <Section title="Aliases">
          {Object.entries(aliases).map(([lang, list]) => (
            <p key={lang} className="text-sm">
              <span className="kicker mr-2">{lang}</span>{list.join(" · ")}
            </p>
          ))}
        </Section>
      )}

      <Section title={`Statements (${statements.length})`}>
        <p className="muted text-xs mb-2">
          Each row is a Wikidata claim. The <b className="text-ink">references</b>{" "}
          show where the system got it from — NLI catalog (P1343=Q118384267),
          Mazal authority IDs, VIAF clusters. Nothing here is unsourced.
          {excludeSaving && <span className="ml-2 text-biu-sky animate-pulse">Saving…</span>}
        </p>
        <ul className="space-y-2">
          {statements.map((s, i) => {
            const quals = s.qualifiers ?? [];
            const refs  = s.references ?? [];
            const prop  = s.property ?? s.property_id;
            const isExcluded = excluded.has(i);
            return (
              <GlassPill as="li" key={i} className={`px-3 py-2 text-sm transition ${
                isExcluded ? "opacity-50" : ""
              }`}>
                <div className={`flex items-baseline gap-2 flex-wrap ${
                  isExcluded ? "line-through" : ""
                }`}>
                  <PropertyPill p={prop} label={s.property_label} store={labelStore} />
                  <ValueRendering snak={s} store={labelStore} onOpenMarc={onOpenMarc} />
                  {s.rank && s.rank !== "normal" && (
                    <span className="ml-2 kicker text-biu-sky">{s.rank}</span>
                  )}
                  <span className="ml-auto shrink-0">
                    {isExcluded ? (
                      <button
                        type="button"
                        onClick={() => void toggleExclude(i)}
                        className="text-[11px] text-biu-sky hover:underline"
                        title="Restore this statement"
                      >
                        Undo
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => void toggleExclude(i)}
                        className="text-[11px] muted hover:text-danger transition"
                        title="Exclude this statement from upload"
                      >
                        ✗ Exclude
                      </button>
                    )}
                  </span>
                </div>

                {quals.length > 0 && (
                  <details className="mt-2">
                    <summary className="muted text-xs cursor-pointer hover:text-ink">
                      {quals.length} qualifier{quals.length === 1 ? "" : "s"}
                    </summary>
                    <ul className="mt-1 ml-4 space-y-1 text-xs">
                      {quals.map((q, qi) => <QualifierRow key={qi} q={q} store={labelStore} />)}
                    </ul>
                  </details>
                )}

                {refs.length > 0 ? (
                  <details className="mt-2" open={refs.length === 1}>
                    <summary className="text-xs cursor-pointer hover:text-ink"
                             style={{ color: "var(--biu-sky)" }}>
                      🔍 {refs.length} reference{refs.length === 1 ? "" : "s"} — where this claim comes from
                    </summary>
                    <ul className="mt-1 ml-4 space-y-1 text-xs">
                      {refs.map((r, ri) => <ReferenceRow key={ri} ref_={r} store={labelStore} />)}
                    </ul>
                  </details>
                ) : (
                  <p className="text-danger text-xs mt-1">
                    ⚠ Unsourced — this claim has no reference. Curate before upload.
                  </p>
                )}
              </GlassPill>
            );
          })}
        </ul>
      </Section>
    </Glass>
  );
}


function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick}
            className={`px-2 py-1 rounded-md transition text-[11px] text-center border border-transparent ${
              active
                ? "bg-white/12 text-ink border-white/15"
                : "muted hover:text-ink hover:bg-white/5"
            }`}>{children}</button>
  );
}


/** One qualifier inside a statement. */
function QualifierRow({ q, store }: { q: Snak; store: LabelStore }) {
  const prop = q.property ?? q.property_id;
  return (
    <li className="flex items-baseline gap-2 flex-wrap">
      <PropertyPill p={prop} label={q.property_label} store={store} />
      <ValueRendering snak={q} store={store} />
    </li>
  );
}


/** One reference. Friendly labels for the common S-props from the
    desktop pipeline (P248 = stated in, P854 = reference URL, P813 =
    retrieved). */
function ReferenceRow({ ref_, store }: { ref_: Snak | { snaks?: Snak[] }; store: LabelStore }) {
  // Reference snaks can be a list or a flat dict — handle both.
  const snaks: Snak[] = Array.isArray((ref_ as { snaks?: Snak[] }).snaks)
    ? (ref_ as { snaks: Snak[] }).snaks
    : [ref_ as Snak];
  return (
    <li className="flex flex-col gap-0.5 border-l-2 border-biu-sky/30 pl-2">
      {snaks.map((s, i) => {
        const prop = s.property ?? s.property_id;
        const friendly = FRIENDLY_S_PROP[prop ?? ""]
                      ?? s.property_label
                      ?? store.label(prop ?? "");
        return (
          <span key={i} className="flex items-baseline gap-2 flex-wrap">
            <span className="muted shrink-0">
              {friendly ?? prop}{prop && friendly ? ` (${prop})` : ""}:
            </span>
            <ValueRendering snak={s} store={store} small />
          </span>
        );
      })}
    </li>
  );
}


// ── all-statements table view ──────────────────────────────────────────


type FlatRow = {
  /** Index into the original build.items list — stable handle for "open in Item view". */
  itemIdx: number;
  /** Index of the statement inside its item. */
  stmtIdx: number;
  item: StudioItem;
  snak: Snak;
};


function StatementTableView({
  items, properties, labelStore, onOpenItem, onOpenMarc,
}: {
  /** Items SURVIVING the sidebar's current filter (current page). */
  items: Array<{ it: StudioItem; idx: number }>;
  /** Distinct properties from the full build — server-precomputed. */
  properties: PropertyInfo[];
  labelStore: LabelStore;
  onOpenItem: (idx: number) => void;
  onOpenMarc: (cn: string) => void;
}) {
  // Local search + per-table filters + sort. Persisted to localStorage
  // so refreshing the page doesn't reset a curator's working state.
  const [tQuery, setTQuery]       = useState(() => localStorage.getItem("mhm.studio.t.q") ?? "");
  const [propFilter, setPropFilter] = useState<string>(() => localStorage.getItem("mhm.studio.t.prop") ?? "");
  const [refFilter, setRefFilter]   = useState<"any"|"sourced"|"unsourced">(
    () => (localStorage.getItem("mhm.studio.t.ref") as "any"|"sourced"|"unsourced") ?? "any");
  const [sKey, setSKey]   = useState<StmtSortKey>(
    () => (localStorage.getItem("mhm.studio.t.sk") as StmtSortKey) ?? "item");
  const [sDir, setSDir]   = useState<SortDir>(
    () => (localStorage.getItem("mhm.studio.t.sd") as SortDir) ?? "asc");
  useEffect(() => { localStorage.setItem("mhm.studio.t.q", tQuery); }, [tQuery]);
  useEffect(() => { localStorage.setItem("mhm.studio.t.prop", propFilter); }, [propFilter]);
  useEffect(() => { localStorage.setItem("mhm.studio.t.ref", refFilter); }, [refFilter]);
  useEffect(() => { localStorage.setItem("mhm.studio.t.sk", sKey); }, [sKey]);
  useEffect(() => { localStorage.setItem("mhm.studio.t.sd", sDir); }, [sDir]);

  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  // Flatten visible items into (item × statement) rows.
  const rows: FlatRow[] = useMemo(() => {
    const out: FlatRow[] = [];
    for (const { it, idx } of items) {
      (it.statements ?? []).forEach((snak, stmtIdx) => {
        out.push({ itemIdx: idx, stmtIdx, item: it, snak });
      });
    }
    return out;
  }, [items]);

  // Lazy-resolve every P/Q id appearing anywhere in the flattened rows.
  // One batched fetch per opening of the Table view.
  useEffect(() => {
    const ids: string[] = [];
    rows.forEach((r) => collectIds(r.snak, ids));
    labelStore.resolve(ids);
  }, [rows, labelStore]);

  // Server-precomputed distinct properties — resolve any missing labels
  // lazily from the label store (fallback for P-ids not in the static dict).
  const propOptions = useMemo(
    () => properties.map((p) => ({
      id: p.id,
      label: p.label || labelStore.label(p.id) || "",
    })),
    [properties, labelStore],
  );

  // Filter + sort.
  const filtered = useMemo(() => {
    const q = tQuery.trim().toLowerCase();
    const result = rows.filter((r) => {
      const p = r.snak.property ?? r.snak.property_id;
      if (propFilter && p !== propFilter) return false;
      const refsLen = (r.snak.references ?? []).length;
      if (refFilter === "sourced"   && refsLen === 0) return false;
      if (refFilter === "unsourced" && refsLen >  0) return false;
      if (q) {
        const v = r.snak.value;
        const haystack = [
          labelOf(r.item),
          r.item.entity_type ?? "",
          r.item.existing_qid ?? "",
          p ?? "",
          r.snak.property_label ?? "",
          r.snak.value_id ?? "",
          r.snak.value_label ?? "",
          typeof v === "string" ? v : "",
        ].join(" ").toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
    const mul = sDir === "asc" ? 1 : -1;
    result.sort((a, b) => {
      let r = 0;
      if (sKey === "item") {
        r = labelOf(a.item).localeCompare(labelOf(b.item), undefined, { sensitivity: "base" });
      } else if (sKey === "type") {
        r = (a.item.entity_type ?? "").localeCompare(b.item.entity_type ?? "");
      } else if (sKey === "property") {
        const ap = a.snak.property_label ?? a.snak.property ?? a.snak.property_id ?? "";
        const bp = b.snak.property_label ?? b.snak.property ?? b.snak.property_id ?? "";
        r = ap.localeCompare(bp);
      } else if (sKey === "value") {
        const av = a.snak.value_label ?? a.snak.value_id ?? String(a.snak.value ?? "");
        const bv = b.snak.value_label ?? b.snak.value_id ?? String(b.snak.value ?? "");
        r = av.localeCompare(bv, undefined, { sensitivity: "base" });
      } else if (sKey === "rank") {
        r = (a.snak.rank ?? "normal").localeCompare(b.snak.rank ?? "normal");
      }
      return r * mul;
    });
    return result;
  }, [rows, tQuery, propFilter, refFilter, sKey, sDir]);

  function toggleSort(k: StmtSortKey) {
    if (sKey !== k) { setSKey(k); setSDir("asc"); return; }
    if (sDir === "asc") { setSDir("desc"); return; }
    setSKey("item"); setSDir("asc");
  }

  function toggleExpand(k: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k); else next.add(k);
      return next;
    });
  }

  return (
    <Glass as="section" className="p-4 space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <input
          value={tQuery} onChange={(e) => setTQuery(e.target.value)}
          placeholder="Search property / value / item label…"
          className="input-glass !py-1.5 text-sm flex-1 min-w-[260px]" />
        <div className="flex items-center gap-2 text-xs">
          <label className="muted">Property</label>
          <select value={propFilter} onChange={(e) => setPropFilter(e.target.value)}
                  className="input-glass !py-1 text-xs max-w-xs">
            <option value="">all properties</option>
            {propOptions.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label ? `${o.label} (${o.id})` : o.id}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <label className="muted">References</label>
          <select value={refFilter}
                  onChange={(e) => setRefFilter(e.target.value as "any"|"sourced"|"unsourced")}
                  className="input-glass !py-1 text-xs">
            <option value="any">any</option>
            <option value="sourced">sourced</option>
            <option value="unsourced">unsourced ⚠</option>
          </select>
        </div>
        <span className="text-[11px] muted ml-auto">
          {filtered.length} of {rows.length} statements
        </span>
      </div>

      <CuratorTableScroll bordered={false}>
        <table className="w-full text-sm border-collapse">
          <thead className="muted text-left sticky top-0 z-10 table-head">
            <tr className="border-b border-white/5">
              <th className="py-2 pr-2 w-8"></th>
              <TableHeader label="Item"      col="item"     sKey={sKey} sDir={sDir} onClick={toggleSort} />
              <TableHeader label="Type"      col="type"     sKey={sKey} sDir={sDir} onClick={toggleSort} />
              <TableHeader label="Property"  col="property" sKey={sKey} sDir={sDir} onClick={toggleSort} />
              <TableHeader label="Value"     col="value"    sKey={sKey} sDir={sDir} onClick={toggleSort} />
              <TableHeader label="Rank"      col="rank"     sKey={sKey} sDir={sDir} onClick={toggleSort} />
              <th className="py-2 pr-3">Qual.</th>
              <th className="py-2 pr-3">Refs</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => {
              const key   = `${r.itemIdx}:${r.stmtIdx}`;
              const open  = expanded.has(key);
              const quals = r.snak.qualifiers ?? [];
              const refs  = r.snak.references ?? [];
              const refSummary = summariseRefs(refs);
              const prop  = r.snak.property ?? r.snak.property_id;
              return (
                <FlatRowView key={key}
                  rowKey={key} open={open} onToggle={() => toggleExpand(key)}
                  r={r} prop={prop} quals={quals} refs={refs}
                  refSummary={refSummary} labelStore={labelStore}
                  onOpenItem={onOpenItem} onOpenMarc={onOpenMarc} />
              );
            })}
            {filtered.length === 0 && (
              <tr><td colSpan={8} className="py-6 text-center muted">
                No statements match. Try clearing the property / search filters above.
              </td></tr>
            )}
          </tbody>
        </table>
      </CuratorTableScroll>
    </Glass>
  );
}


function TableHeader({
  label, col, sKey, sDir, onClick,
}: {
  label: string; col: StmtSortKey;
  sKey: StmtSortKey; sDir: SortDir;
  onClick: (k: StmtSortKey) => void;
}) {
  const active = sKey === col;
  return (
    <th className="py-2 pr-3 select-none">
      <button onClick={() => onClick(col)}
              className="inline-flex items-center gap-1 hover:text-ink transition">
        <span>{label}</span>
        {active
          ? <span className="text-biu-sky text-[10px]">{sDir === "asc" ? "↑" : "↓"}</span>
          : <span className="muted text-[9px]">⇅</span>}
      </button>
    </th>
  );
}


function FlatRowView({
  rowKey, open, onToggle, r, prop, quals, refs, refSummary,
  labelStore, onOpenItem, onOpenMarc,
}: {
  rowKey: string; open: boolean; onToggle: () => void;
  r: FlatRow; prop: string | undefined;
  quals: Snak[]; refs: Array<Snak | { snaks?: Snak[] }>;
  refSummary: string;
  labelStore: LabelStore;
  onOpenItem: (idx: number) => void;
  onOpenMarc: (cn: string) => void;
}) {
  const itemLabel = labelOf(r.item) || "(no label)";
  return (
    <>
      <tr className="border-b border-white/5 hover:bg-white/[0.03] transition align-top">
        <td className="py-2 pr-2">
          {(quals.length > 0 || refs.length > 0) && (
            <button onClick={onToggle}
                    className="muted hover:text-ink text-xs"
                    title={open ? "Collapse details" : "Expand qualifiers + references"}>
              {open ? "▾" : "▸"}
            </button>
          )}
        </td>
        <td className="py-2 pr-3">
          <button onClick={() => onOpenItem(r.itemIdx)}
                  className="text-left hover:text-ink hover:underline">
            <span className="block max-w-[220px] truncate">{itemLabel}</span>
            {r.item.existing_qid && (
              <span className="font-mono text-[10px] text-biu-sky">↻ {r.item.existing_qid}</span>
            )}
          </button>
        </td>
        <td className="py-2 pr-3"><span className="kicker">{r.item.entity_type ?? "?"}</span></td>
        <td className="py-2 pr-3"><PropertyPill p={prop} label={r.snak.property_label} store={labelStore} /></td>
        <td className="py-2 pr-3">
          <ValueRendering snak={r.snak} store={labelStore} onOpenMarc={onOpenMarc} />
        </td>
        <td className="py-2 pr-3">
          {r.snak.rank && r.snak.rank !== "normal"
            ? <span className="kicker text-biu-sky">{r.snak.rank}</span>
            : <span className="muted text-xs">normal</span>}
        </td>
        <td className="py-2 pr-3 text-xs">
          {quals.length > 0 ? <span className="text-ink">{quals.length}</span> : <span className="muted">—</span>}
        </td>
        <td className="py-2 pr-3 text-xs">
          {refs.length > 0
            ? <span title={refSummary} className="text-ink">{refs.length} <span className="muted">·</span> {refSummary}</span>
            : <span className="text-danger" title="No reference attached">⚠ unsourced</span>}
        </td>
      </tr>
      {open && (quals.length > 0 || refs.length > 0) && (
        <tr className="border-b border-white/5 bg-white/[0.02]">
          <td className="py-2 pr-2"></td>
          <td colSpan={7} className="py-2 pr-3">
            {quals.length > 0 && (
              <div className="mb-2">
                <div className="kicker mb-1">Qualifiers</div>
                <ul className="space-y-1 text-xs">
                  {quals.map((q, qi) => <QualifierRow key={`${rowKey}-q-${qi}`} q={q} store={labelStore} />)}
                </ul>
              </div>
            )}
            {refs.length > 0 && (
              <div>
                <div className="kicker mb-1">References — where this claim comes from</div>
                <ul className="space-y-1 text-xs">
                  {refs.map((rf, ri) => <ReferenceRow key={`${rowKey}-r-${ri}`} ref_={rf} store={labelStore} />)}
                </ul>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}


/** Build a short "NLI · VIAF" summary from a list of reference blocks
 *  by reading their P248 (stated in) values. Used only for the Refs
 *  column to give curators a glance at the sources without expanding. */
function summariseRefs(refs: Array<Snak | { snaks?: Snak[] }>): string {
  const sources = new Set<string>();
  for (const r of refs) {
    const snaks: Snak[] = Array.isArray((r as { snaks?: Snak[] }).snaks)
      ? (r as { snaks: Snak[] }).snaks
      : [r as Snak];
    for (const s of snaks) {
      const p = s.property ?? s.property_id;
      if (p === "P248") {
        const id = s.value_id ?? (typeof s.value === "string" ? s.value : "");
        if (id) sources.add(_REF_SOURCE_LABELS[id] ?? id);
      } else if (p === "P854" && typeof s.value === "string") {
        try   { sources.add(new URL(s.value).hostname.replace(/^www\./, "")); }
        catch { /* not a URL */ }
      }
    }
  }
  return Array.from(sources).slice(0, 4).join(" · ") || "—";
}


const _REF_SOURCE_LABELS: Record<string, string> = {
  Q118384267: "Ktiv",         // NLI manuscript catalog
  Q54919:     "VIAF",
  Q1378214:   "GND",
  Q1389135:   "BnF",
};


function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <div className="kicker mb-2">{title}</div>
      {children}
    </section>
  );
}


function KvList({ items }: { items: Record<string, string> }) {
  return (
    <dl className="grid grid-cols-[60px_1fr] gap-x-3 text-sm">
      {Object.entries(items).map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="kicker">{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}


function Stat({
  label, value, highlight, sub,
}: { label: string; value: number | string; highlight?: boolean; sub?: string }) {
  return (
    <GlassPill as="div" className="px-3 py-2">
      <div className="kicker">{label}</div>
      <div className={`text-xl font-semibold ${highlight ? "text-biu-sky" : ""}`}>{value}</div>
      {sub && <div className="muted text-[10px] leading-tight mt-0.5">{sub}</div>}
    </GlassPill>
  );
}


// ── helpers ─────────────────────────────────────────────────────────────


function labelOf(it: StudioItem): string {
  const l = it.labels ?? {};
  return l.en || l.he || Object.values(l)[0] || it.local_id || "";
}



function statusTone(status: string): string {
  if (status === "success" || status === "updated" || status === "exists") return "text-biu-sky";
  if (status === "skipped" || status === "pending") return "muted";
  if (status === "failed") return "text-danger";
  return "text-warn";
}
