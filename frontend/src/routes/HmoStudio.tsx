import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {Layout} from "@/components/Layout";
import {ApiError} from "@/api/client";
import {MarcFieldEditorDialog} from "@/components/MarcFieldEditorDialog";
import {Runs} from "@/api/runs";
import {loadHmoCoverage, ensureRunJob} from "@/utils/waitForRunJob";
import {SectionExportMenu} from "@/components/export/SectionExportMenu";
import {SectionImportButton} from "@/components/import/SectionImportButton";
import {Glass, GlassPill} from "@/components/glass";
import {JobProgressInline} from "@/components/jobs/JobProgressInline";
import {CuratorTableScroll} from "@/components/CuratorTableScroll";
import {SchemaBootstrapPanel} from "@/components/hmo/SchemaBootstrapPanel";
import {HmoItemsPanel} from "@/components/hmo/HmoItemsPanel";
import {
  CoverageClassDetailPopover,
  CoverageClassRow,
  useCoverageExplainPopover,
} from "@/components/hmo/CoverageClassRow";
import {RdfGraphExplorer} from "@/components/rdf/RdfGraphExplorer";
import {useProjectEvents} from "@/api/realtime";
import {type RunJobSnapshot} from "@/api/runJobs";
import {isJobActive, useRunJobs} from "@/stores/runJobs";
import {useRunJobAttachment} from "@/hooks/useRunJobAttachment";
import {
  HmoStudio,
  manifestBuildResultFromJob,
  manifestUploadResultFromJob,
  type HmoBuildResult,
  type HmoCoverageReport,
  type HmoManifestSummary,
  type HmoStudioStatus,
  type HmoUploadResult,
} from "@/api/hmoStudio";


type Busy = null | "build" | "upload" | "coverage";
type StudioTab = "items" | "coverage" | "rdf" | "manifests";

const STUDIO_TABS: Array<{id: StudioTab; label: string}> = [
  {id: "items", label: "Items"},
  {id: "coverage", label: "Wikidata coverage"},
  {id: "rdf", label: "RDF graph"},
  {id: "manifests", label: "Manifests"},
];


export default function HmoStudioRoute() {
  const { runId } = useParams<{ runId: string }>();

  const [status, setStatus] = useState<HmoStudioStatus | null>(null);
  const [coverage, setCoverage] = useState<HmoCoverageReport | null>(null);
  const [build, setBuild] = useState<HmoBuildResult | null>(null);
  const [upload, setUpload] = useState<HmoUploadResult | null>(null);

  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);
  const [coverageProgress, setCoverageProgress] = useState<string | null>(null);
  const coverageAttemptedRef = useRef(false);
  const [dryRun, setDryRun] = useState(true);
  const [recordCns, setRecordCns] = useState<string[]>([]);
  const [recordQuery, setRecordQuery] = useState("");
  const [editCn, setEditCn] = useState<string | null>(null);
  const [showRecordPicker, setShowRecordPicker] = useState(false);
  const [itemBuildToken, setItemBuildToken] = useState(0);
  const [itemBuildPresent, setItemBuildPresent] = useState(false);
  const [projectId, setProjectId] = useState<string | undefined>(undefined);
  const [manifestJob, setManifestJob] = useState<RunJobSnapshot | null>(null);
  const [manifestUploadJob, setManifestUploadJob] = useState<RunJobSnapshot | null>(null);
  const upsertJob = useRunJobs((s) => s.upsertJob);
  const [studioTab, setStudioTab] = useState<StudioTab>("items");
  const [manifestList, setManifestList] = useState<HmoManifestSummary[]>([]);
  const [manifestPreview, setManifestPreview] = useState<{
    shelfmark: string;
    json: string;
  } | null>(null);
  const [manifestListBusy, setManifestListBusy] = useState(false);


  // ── refreshers ─────────────────────────────────────────────────────────

  const refreshStatus = useCallback(async () => {
    if (!runId) return;
    try {
      setStatus(await HmoStudio.status(runId));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }, [runId]);

  const loadCoverage = useCallback(async () => {
    if (!runId) return;
    setBusy("coverage"); setError(null); setCoverageProgress(null);
    try {
      // On a cold cache the backend enqueues a background job instead of
      // building inline (that used to hold the request — and its DB
      // connection — open past Heroku's 30s router timeout on large
      // runs). loadHmoCoverage polls the job and re-fetches once it's
      // done, so this may take a while but never times out the request.
      setCoverage(await loadHmoCoverage(runId, () => HmoStudio.coverage(runId), {
        onProgress: setCoverageProgress,
      }));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally { setBusy(null); setCoverageProgress(null); }
  }, [runId]);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    Runs.listRecords(runId)
      .then((cns) => { if (!cancelled) setRecordCns(cns); })
      .catch(() => { /* non-fatal */ });
    return () => { cancelled = true; };
  }, [runId]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    Runs.get(runId)
      .then((run) => { if (!cancelled) setProjectId(run.project_id); })
      .catch(() => { /* non-fatal — WS push just stays disabled */ });
    return () => { cancelled = true; };
  }, [runId]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    HmoStudio.itemStatus(runId)
      .then((st) => {
        if (cancelled) return;
        setItemBuildPresent(st.build_present);
      })
      .catch(() => { /* non-fatal */ });
    return () => { cancelled = true; };
  }, [runId, itemBuildToken]);

  // Live job progress: the schema-bootstrap background job pushes updates
  // through the project's existing WebSocket room (see run_job_service.py
  // ::_notify_job_update). Polling in useRunJobAttachment stays the source
  // of truth; this just shaves off the up-to-2s poll latency.
  useProjectEvents(projectId, (msg) => {
    if (msg.type === "run_job_update" && msg.job) {
      useRunJobs.getState().upsertJob(msg.job);
    }
  });

  // Load coverage when the coverage tab is opened (once per session unless Refresh).
  useEffect(() => {
    if (
      studioTab === "coverage"
      && status?.rdf_present && coverage === null && busy !== "coverage"
      && !coverageAttemptedRef.current
    ) {
      coverageAttemptedRef.current = true;
      void loadCoverage();
    }
  }, [studioTab, status, coverage, busy, loadCoverage]);

  const refreshManifestList = useCallback(async () => {
    if (!runId) return;
    setManifestListBusy(true);
    try {
      const res = await HmoStudio.listManifests(runId);
      setManifestList(res.manifests);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setManifestListBusy(false);
    }
  }, [runId]);

  useEffect(() => {
    if (studioTab !== "manifests" || !runId) return;
    void refreshManifestList();
  }, [studioTab, runId, refreshManifestList, status?.manifest_count]);

  async function openManifestPreview(shelfmark: string) {
    if (!runId) return;
    setError(null);
    try {
      const raw = await HmoStudio.getManifest(runId, shelfmark);
      setManifestPreview({
        shelfmark,
        json: JSON.stringify(raw, null, 2),
      });
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  const {setTrackedJobId: setManifestTrackedId, ensureJobPolling: ensureManifestPolling} =
    useRunJobAttachment(runId, "hmo_manifest_build", (j) => {
      setManifestJob(j);
      if (j.status === "succeeded") {
        const fromJob = manifestBuildResultFromJob(j);
        if (fromJob) {
          setBuild(fromJob);
          setManifestList(fromJob.manifests ?? []);
        }
        void refreshStatus();
        setBusy((b) => (b === "build" ? null : b));
      }
      if (j.status === "failed" || j.status === "cancelled") {
        setError(j.error ?? (j.status === "cancelled" ? "Manifest build cancelled." : "Manifest build failed."));
        setBusy((b) => (b === "build" ? null : b));
      }
    });

  const {setTrackedJobId: setManifestUploadTrackedId, ensureJobPolling: ensureManifestUploadPolling} =
    useRunJobAttachment(runId, "hmo_manifest_upload", (j) => {
      setManifestUploadJob(j);
      if (j.status === "succeeded") {
        const fromJob = manifestUploadResultFromJob(j);
        if (fromJob) setUpload(fromJob);
        void refreshStatus();
        setBusy((b) => (b === "upload" ? null : b));
      }
      if (j.status === "failed" || j.status === "cancelled") {
        setError(j.error ?? (j.status === "cancelled" ? "Manifest upload cancelled." : "Manifest upload failed."));
        setBusy((b) => (b === "upload" ? null : b));
      }
    });

  // ── actions ────────────────────────────────────────────────────────────

  async function doBuild() {
    if (!runId) return;
    setBusy("build"); setError(null);
    try {
      const started = await ensureRunJob(runId, "hmo_manifest_build", {});
      upsertJob(started);
      setManifestJob(started);
      setManifestTrackedId(started.id);
      ensureManifestPolling();
      if (!isJobActive(started.status)) {
        setBusy(null);
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
      setBusy(null);
    }
  }

  async function doUpload() {
    if (!runId) return;
    setBusy("upload"); setError(null);
    try {
      const started = await ensureRunJob(runId, "hmo_manifest_upload", {dry_run: dryRun});
      upsertJob(started);
      setManifestUploadJob(started);
      setManifestUploadTrackedId(started.id);
      ensureManifestUploadPolling();
      if (!isJobActive(started.status)) {
        setBusy(null);
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
      setBusy(null);
    }
  }

  // ── derived ────────────────────────────────────────────────────────────

  const statePill = useMemo(() => {
    if (!status) return { label: "loading…", tone: "muted" };
    switch (status.state) {
      case "uploaded": return { label: "uploaded", tone: "text-biu-sky" };
      case "built":    return { label: "built",    tone: "text-warn" };
      case "error":    return { label: "error",    tone: "text-danger" };
      case "idle":
      default:         return { label: "idle",     tone: "muted" };
    }
  }, [status]);

  const wikibaseConfigured = !!status?.wikibase_configured;
  const canonicalStatus = getCanonicalStatus(status);

  return (
    <Layout>
      <div className="space-y-6">
        {/* Breadcrumb + state pill */}
        <Glass as="section" className="p-5 flex flex-wrap items-center gap-3 justify-between">
          <div className="flex flex-wrap items-baseline gap-2">
            <Link to="/" className="muted hover:text-ink text-sm">Projects</Link>
            <span className="muted">/</span>
            {runId && (
              <Link to={`/runs/${runId}/overview`} className="muted hover:text-ink text-sm">
                Run {runId.slice(0, 8)}
              </Link>
            )}
            <span className="muted">/</span>
            <h1 className="text-lg font-semibold">HMO Wikibase Studio</h1>
          </div>
          <div className="flex items-center gap-2">
            <GlassPill className={`px-3 py-0.5 text-[10px] kicker ${statePill.tone}`}>
              {statePill.label}
            </GlassPill>
            <GlassPill className={`px-3 py-0.5 text-[10px] kicker ${canonicalStatus.tone}`} title={canonicalStatus.detail}>
              {canonicalStatus.label}
            </GlassPill>
          </div>
        </Glass>

        {error && (
          <Glass as="p" variant="compact" className="p-3 text-sm text-danger">{error}</Glass>
        )}

        <nav
          className="flex flex-wrap gap-1 border-b border-white/10 pb-2"
          role="tablist"
          aria-label="HMO Studio sections"
          data-testid="hmo-studio-tabs"
        >
          {STUDIO_TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={studioTab === t.id}
              data-testid={`hmo-studio-tab-${t.id}`}
              onClick={() => setStudioTab(t.id)}
              className={`text-sm px-3 py-1.5 rounded-lg transition-colors ${
                studioTab === t.id
                  ? "bg-biu-sky/15 text-biu-sky border border-biu-sky/30"
                  : "muted hover:text-ink hover:bg-white/5 border border-transparent"
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>

        {studioTab === "items" && (
          <div className="space-y-6" role="tabpanel" data-testid="hmo-studio-panel-items">
            <Glass as="section" className="p-6 space-y-4" aria-labelledby="hmo-workflow-heading">
              <div>
                <div className="kicker">Your review workflow</div>
                <h2 id="hmo-workflow-heading" className="text-lg font-medium">Prepare, review, preview, publish</h2>
                <p className="muted text-sm mt-1">
                  Follow these four steps to check catalogue entries before they are published.
                </p>
              </div>
              <ol className="grid gap-3 md:grid-cols-4">
                {[
                  ["Prepare", "Prepare catalogue data and generated HMO entries."],
                  ["Review", "Check entries, quality, and editorial decisions."],
                  ["Preview", "See what will change before publishing."],
                  ["Publish", "Publish approved entries to the HMO catalogue."],
                ].map(([title, description], index) => (
                  <li key={title} className="border border-white/10 rounded-lg p-3 space-y-2">
                    <span className="kicker">{index + 1}</span>
                    <h3 className="font-medium">{title}</h3>
                    <p className="muted text-xs leading-relaxed">{description}</p>
                  </li>
                ))}
              </ol>
            </Glass>

            {runId && (
              <HmoItemsPanel
                runId={runId}
                projectId={projectId}
                buildPresent={itemBuildPresent}
                refreshToken={itemBuildToken}
                rdfPresent={!!status?.rdf_present}
                wikibaseConfigured={wikibaseConfigured}
                onLifecycleChange={() => {
                  setItemBuildToken((t) => t + 1);
                  setItemBuildPresent(true);
                }}
              />
            )}

            <details className="space-y-3">
              <summary className="cursor-pointer text-sm font-medium">Advanced: catalogue schema maintenance</summary>
              <SchemaBootstrapPanel runId={runId} />
            </details>

            <Glass as="section" className="p-6 space-y-3">
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <div>
                  <div className="kicker">MARC records</div>
                  <h3 className="text-lg font-medium">Edit source catalog data</h3>
                  <p className="muted text-sm mt-1">
                    Hand-edit MARC fields before rebuilding the RDF graph and manifests.
                  </p>
                </div>
                <button type="button" onClick={() => setShowRecordPicker((v) => !v)}
                        data-testid="hmo-edit-records-toggle"
                        className="button-ghost text-sm">
                  {showRecordPicker ? "Hide records" : "Edit records…"}
                </button>
              </div>
              {showRecordPicker && (
                <div className="space-y-2">
                  <input value={recordQuery} onChange={(e) => setRecordQuery(e.target.value)}
                         placeholder="Search control number…"
                         className="input-glass text-sm w-full max-w-md" />
                  <ul className="max-h-48 overflow-auto border border-white/5 rounded-lg text-sm">
                    {recordCns
                      .filter((cn) => !recordQuery || cn.includes(recordQuery))
                      .slice(0, 200)
                      .map((cn) => (
                        <li key={cn} className="border-b border-white/5 px-3 py-1.5 flex justify-between">
                          <span className="font-mono text-xs">{cn}</span>
                          <button type="button" onClick={() => setEditCn(cn)}
                                  data-testid={`hmo-edit-marc-${cn}`}
                                  className="button-ghost text-xs">
                            Edit MARC
                          </button>
                        </li>
                      ))}
                  </ul>
                </div>
              )}
            </Glass>

            <details className="space-y-3">
              <summary className="cursor-pointer text-sm font-medium">Advanced: server connection</summary>
              <Glass as="section" className="p-6 space-y-2">
                <div className="kicker">HMO catalogue connection</div>
                <h3 className="text-lg font-medium">Live publication prerequisite</h3>
                <p className="muted text-sm leading-relaxed">
                  Publishing uses the project&apos;s secure server connection. Previewing changes does not require a live connection.
                </p>
                <GlassPill
                  className={`inline-block px-3 py-0.5 text-[10px] kicker ${
                    wikibaseConfigured ? "text-biu-sky" : "text-warn"
                  }`}
                >
                  {wikibaseConfigured ? "✓ server configured" : "⚠ not configured — contact admin"}
                </GlassPill>
              </Glass>
            </details>
          </div>
        )}

        {studioTab === "coverage" && (
          <Glass as="section" className="p-6 space-y-3" role="tabpanel" data-testid="hmo-studio-panel-coverage">
            <div className="flex justify-between items-baseline gap-3">
              <div>
                <div className="kicker">HMO → Wikidata projection coverage</div>
                <h3 className="text-lg font-medium">
                  {coverage
                    ? `${coverage.rdf_class_count} RDF classes · ${coverage.wikidata_item_count} projected items`
                    : "Coverage report"}
                </h3>
              </div>
              <button onClick={loadCoverage}
                      disabled={busy !== null || !status?.rdf_present}
                      className="button-ghost text-xs">
                {busy === "coverage" ? "Loading…" : "Refresh"}
              </button>
            </div>

            {busy === "coverage" && coverageProgress && (
              <p className="muted text-xs">{coverageProgress}</p>
            )}

            {!status?.rdf_present && (
              <p className="muted text-sm">
                No RDF graph for this run yet. Build the RDF Graph on the{" "}
                <Link to={`/runs/${runId ?? ""}/rdf`} className="text-biu-sky hover:underline">
                  RDF Graph page
                </Link>{" "}
                before viewing coverage.
              </p>
            )}

            {coverage && (
              <CoverageTable report={coverage} />
            )}
          </Glass>
        )}

        {studioTab === "rdf" && runId && (
          <Glass as="section" className="p-6" role="tabpanel" data-testid="hmo-studio-panel-rdf">
            <RdfGraphExplorer runId={runId} height={520} />
          </Glass>
        )}

        {studioTab === "manifests" && (
          <Glass as="section" className="p-6 space-y-4" role="tabpanel" data-testid="hmo-studio-panel-manifests">
            <div>
              <div className="kicker">IIIF manifests</div>
              <h3 className="text-lg font-medium">
                {status?.manifest_count ?? manifestList.length} manifest
                {(status?.manifest_count ?? manifestList.length) === 1 ? "" : "s"} generated
              </h3>
              <p className="muted text-sm leading-relaxed mt-1">
                Each manuscript produces one IIIF Presentation API 3.0 manifest
                with the HMO scholarly overlay. Click a row to preview the JSON.
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-3 pt-2">
              <button onClick={doBuild}
                      disabled={busy !== null || !status?.rdf_present || (manifestJob != null && isJobActive(manifestJob.status))}
                      className="button-primary text-sm">
                {busy === "build" || (manifestJob != null && isJobActive(manifestJob.status))
                  ? "Building…"
                  : "Build manifests"}
              </button>
              {runId && (
                <SectionExportMenu
                  section="wikibase"
                  runId={runId}
                  availableFormats={["json", "csv", "ttl"]}
                />
              )}
              {runId && (
                <SectionImportButton
                  section="wikibase"
                  runId={runId}
                  accept=".json"
                />
              )}

              <div className="flex items-center gap-2 text-sm muted">
                <label className="flex items-center gap-1">
                  <input type="checkbox"
                         checked={dryRun}
                         onChange={(e) => setDryRun(e.target.checked)}
                         disabled={busy !== null} />
                  Dry run
                </label>
                <button onClick={doUpload}
                        disabled={busy !== null || (status?.manifest_count ?? 0) === 0
                                                || (!dryRun && !wikibaseConfigured)}
                        className={dryRun ? "button-ghost text-sm" : "button-primary text-sm"}>
                  {busy === "upload"
                    ? (dryRun ? "Previewing…" : "Uploading…")
                    : (dryRun ? "Preview upload" : "Upload manifests")}
                </button>
              </div>
              <button
                type="button"
                className="button-ghost text-xs"
                disabled={manifestListBusy}
                onClick={() => void refreshManifestList()}
              >
                {manifestListBusy ? "Refreshing…" : "Refresh list"}
              </button>
            </div>

            {manifestJob && (
              <JobProgressInline
                job={manifestJob}
                labels={{
                  running: "Building manifests…",
                  succeeded: "Manifest build complete:",
                  failed: "Manifest build failed:",
                  cancelled: "Manifest build cancelled:",
                }}
              />
            )}
            {manifestUploadJob && (
              <JobProgressInline
                job={manifestUploadJob}
                labels={{
                  running: dryRun ? "Previewing manifest upload…" : "Uploading manifests…",
                  succeeded: dryRun ? "Manifest preview complete:" : "Manifest upload complete:",
                  failed: "Manifest upload failed:",
                  cancelled: "Manifest upload cancelled:",
                }}
              />
            )}

            {build && (
              <p className="text-xs muted pt-1">
                Last build: {build.manifest_count} manifests · {build.total_canvases} canvases ·
                {" "}{build.total_ranges} CU ranges · {build.total_annotations} annotations
              </p>
            )}

            {manifestList.length > 0 ? (
              <div className="grid gap-4 lg:grid-cols-2">
                <CuratorTableScroll>
                  <table className="w-full text-sm">
                    <thead className="bg-white/3 text-xs uppercase muted tracking-wider sticky top-0 z-10 table-head">
                      <tr>
                        <th className="text-left px-3 py-2">Shelfmark</th>
                        <th className="text-right px-3 py-2">Canvases</th>
                        <th className="text-right px-3 py-2">Ranges</th>
                        <th className="text-right px-3 py-2">Annotations</th>
                      </tr>
                    </thead>
                    <tbody>
                      {manifestList.map((m) => (
                        <tr
                          key={m.file}
                          className={`border-t border-white/5 cursor-pointer hover:bg-white/5 ${
                            manifestPreview?.shelfmark === m.shelfmark ? "bg-biu-sky/10" : ""
                          }`}
                          data-testid={`hmo-manifest-row-${m.shelfmark.replace(/[^A-Za-z0-9_-]+/g, "_")}`}
                          onClick={() => { void openManifestPreview(m.shelfmark); }}
                        >
                          <td className="px-3 py-2 font-mono text-xs">{m.shelfmark}</td>
                          <td className="px-3 py-2 text-right font-mono text-xs">{m.canvas_count}</td>
                          <td className="px-3 py-2 text-right font-mono text-xs">{m.range_count}</td>
                          <td className="px-3 py-2 text-right font-mono text-xs">{m.annotation_count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </CuratorTableScroll>
                <Glass variant="compact" className="p-3 space-y-2 min-h-[16rem]">
                  <div className="flex items-baseline justify-between gap-2">
                    <h4 className="text-sm font-medium">
                      {manifestPreview
                        ? `Preview · ${manifestPreview.shelfmark}`
                        : "Manifest preview"}
                    </h4>
                    {manifestPreview && (
                      <button
                        type="button"
                        className="button-ghost text-xs"
                        onClick={() => setManifestPreview(null)}
                      >
                        Close
                      </button>
                    )}
                  </div>
                  {manifestPreview ? (
                    <pre
                      className="text-[11px] font-mono overflow-auto max-h-[28rem] whitespace-pre-wrap break-all"
                      data-testid="hmo-manifest-preview"
                    >
                      {manifestPreview.json}
                    </pre>
                  ) : (
                    <p className="muted text-sm">Select a manifest row to inspect its IIIF JSON.</p>
                  )}
                </Glass>
              </div>
            ) : (
              <p className="muted text-sm">
                {(status?.manifest_count ?? 0) > 0
                  ? "Loading manifest list…"
                  : "No manifests yet — build them from the RDF graph."}
              </p>
            )}

            {upload && (
              <UploadReportPanel report={upload} />
            )}

            {!upload && status?.last_upload && (
              <UploadReportPanel
                report={status.last_upload}
                cachedAt={status.last_upload_at}
              />
            )}
          </Glass>
        )}
      </div>

      {editCn && runId && (
        <MarcFieldEditorDialog
          runId={runId}
          controlNumber={editCn}
          onClose={() => setEditCn(null)}
        />
      )}
    </Layout>
  );
}


function getCanonicalStatus(status: HmoStudioStatus | null): {
  label: string;
  detail: string;
  tone: string;
} {
  if (!status) {
    return {
      label: "confirmation loading",
      detail: "Checking whether the published catalogue entries have been confirmed.",
      tone: "muted",
    };
  }
  if (!status.canonical_live_count) {
    return {
      label: "not yet confirmed",
      detail: "No published catalogue entries have been confirmed yet.",
      tone: "text-warn",
    };
  }
  if (status.canonical_ready) {
    return {
      label: "all entries confirmed",
      detail: "Every built entry has a confirmed live HMO catalogue copy.",
      tone: "text-emerald-300",
    };
  }
  return {
    label: `${status.canonical_live_count} entries confirmed`,
    detail: "Some entries have a confirmed live copy, but the complete set is not ready.",
    tone: "text-warn",
  };
}


// ── CoverageTable ────────────────────────────────────────────────────────


function CoverageTable({report}: {report: HmoCoverageReport}) {
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const {anchor, open, close} = useCoverageExplainPopover();
  const filtered = useMemo(() => {
    if (statusFilter === "all") return report.classes;
    return report.classes.filter((c) => c.projection_status === statusFilter);
  }, [report.classes, statusFilter]);

  const summary = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const c of report.classes) {
      counts[c.projection_status] = (counts[c.projection_status] || 0) + 1;
    }
    return counts;
  }, [report.classes]);

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-wrap gap-1.5 text-xs">
          {(
            [
              ["all", "All"],
              ["direct_wikidata_item", "Direct"],
              ["summarized_in_wikidata", "Summarised"],
              ["hmo_or_wikibase_only", "HMO-only"],
              ["unknown", "Unknown"],
            ] as const
          ).map(([key, label]) => {
            const n = key === "all" ? report.classes.length : summary[key] || 0;
            const active = statusFilter === key;
            return (
              <button key={key}
                      onClick={() => setStatusFilter(key)}
                      className={`px-2.5 py-1 rounded-full transition ${
                        active ? "bg-white/12 text-ink" : "muted hover:text-ink"
                      }`}>
                {label} · {n}
              </button>
            );
          })}
        </div>
        <p className="text-[11px] muted">Hover for a summary · click a row for the full projection note</p>
      </div>

      <CuratorTableScroll>
        <table className="w-full text-sm">
          <thead className="bg-white/3 text-xs uppercase muted tracking-wider sticky top-0 z-10 table-head">
            <tr>
              <th className="text-left px-3 py-2">HMO class</th>
              <th className="text-left px-3 py-2">Projection</th>
              <th className="text-right px-3 py-2">HMO nodes</th>
              <th className="text-right px-3 py-2">Items</th>
              <th className="text-left px-3 py-2">Wikidata properties</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((row) => (
              <CoverageClassRow
                key={row.class_uri}
                row={row}
                onExplain={open}
              />
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-4 text-center muted text-sm">
                  No classes match this filter.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </CuratorTableScroll>
      {anchor && (
        <CoverageClassDetailPopover
          row={anchor.row}
          x={anchor.x}
          y={anchor.y}
          onClose={close}
        />
      )}
    </div>
  );
}


// ── UploadReportPanel ────────────────────────────────────────────────────


function UploadReportPanel({
  report, cachedAt,
}: {
  report: HmoUploadResult;
  cachedAt?: string | null;
}) {
  const [expand, setExpand] = useState(false);

  return (
    <div className="border-t border-white/5 pt-3 space-y-2">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <p className="text-sm">
          <span className="muted">Last upload:</span>{" "}
          <b className="text-ink">
            {report.dry_run ? "dry-run" : "live"}
          </b>{" "}
          · <span className="text-biu-sky">{report.uploaded} uploaded</span>
          {report.unchanged > 0 && <> · {report.unchanged} unchanged</>}
          {report.failed > 0 && <> · <span className="text-danger">{report.failed} failed</span></>}
          {cachedAt && <> · <span className="muted">{cachedAt}</span></>}
        </p>
        <button onClick={() => setExpand((v) => !v)} className="button-ghost text-xs">
          {expand ? "Hide details" : "Show details"}
        </button>
      </div>

      {expand && (
        <CuratorTableScroll>
          <table className="w-full text-sm">
            <thead className="bg-white/3 text-xs uppercase muted tracking-wider sticky top-0 z-10 table-head">
              <tr>
                <th className="text-left px-3 py-2">Shelfmark</th>
                <th className="text-left px-3 py-2">Status</th>
                <th className="text-left px-3 py-2">Page</th>
                <th className="text-left px-3 py-2">Message</th>
              </tr>
            </thead>
            <tbody>
              {report.outcomes.map((o) => (
                <tr key={o.shelfmark} className="border-t border-white/5">
                  <td className="px-3 py-2 font-mono text-xs">{o.shelfmark}</td>
                  <td className="px-3 py-2"><UploadStatusPill status={o.status} /></td>
                  <td className="px-3 py-2 text-xs truncate max-w-[280px]">
                    {o.page_url
                      ? <a href={o.page_url} target="_blank" rel="noopener"
                           className="text-biu-sky hover:underline">{o.page_url}</a>
                      : <span className="muted">—</span>}
                  </td>
                  <td className="px-3 py-2 text-xs muted">{o.message}</td>
                </tr>
              ))}
              {report.outcomes.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-3 py-4 text-center muted text-sm">
                    No outcomes recorded.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </CuratorTableScroll>
      )}
    </div>
  );
}


function UploadStatusPill({ status }: { status: string }) {
  const tone =
    status === "created" || status === "updated" ? "text-biu-sky" :
    status === "dry_run"                          ? "text-warn" :
    status === "unchanged"                        ? "muted" :
                                                    "text-danger";
  return (
    <GlassPill className={`px-2 py-0.5 text-[10px] kicker ${tone}`}>
      {status}
    </GlassPill>
  );
}
