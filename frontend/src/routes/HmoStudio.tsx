import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {Layout} from "@/components/Layout";
import {ApiError} from "@/api/client";
import {MarcFieldEditorDialog} from "@/components/MarcFieldEditorDialog";
import {Runs} from "@/api/runs";
import {
  HmoStudio,
  type HmoBuildResult,
  type HmoCoverageReport,
  type HmoStudioStatus,
  type HmoUploadResult,
} from "@/api/hmoStudio";
import {loadHmoCoverage} from "@/utils/waitForRunJob";
import {SectionExportMenu} from "@/components/export/SectionExportMenu";
import {SectionImportButton} from "@/components/import/SectionImportButton";
import {Glass, GlassPill} from "@/components/glass";
import {CuratorTableScroll} from "@/components/CuratorTableScroll";
import {SchemaBootstrapPanel} from "@/components/hmo/SchemaBootstrapPanel";
import {HmoItemsPanel} from "@/components/hmo/HmoItemsPanel";
import {GraphOverviewSummary} from "@/components/rdf/GraphOverviewSummary";
import {useProjectEvents} from "@/api/realtime";
import {useRunJobs} from "@/stores/runJobs";


type Busy = null | "build" | "upload" | "coverage";


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

  // Auto-load coverage once when the RDF is present. Guarded by a ref
  // (not just `coverage === null`) so a failed attempt — including one
  // whose background job errored out — does not retry in a tight loop;
  // the user can still retry manually via the "Refresh" button.
  useEffect(() => {
    if (
      status?.rdf_present && coverage === null && busy !== "coverage"
      && !coverageAttemptedRef.current
    ) {
      coverageAttemptedRef.current = true;
      void loadCoverage();
    }
  }, [status, coverage, busy, loadCoverage]);

  // ── actions ────────────────────────────────────────────────────────────

  async function doBuild() {
    if (!runId) return;
    setBusy("build"); setError(null);
    try {
      const result = await HmoStudio.buildManifests(runId);
      setBuild(result);
      // Coverage may now reflect different counts; refresh both.
      await refreshStatus();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally { setBusy(null); }
  }

  async function doUpload() {
    if (!runId) return;
    setBusy("upload"); setError(null);
    try {
      const result = await HmoStudio.uploadManifests(runId, dryRun);
      setUpload(result);
      await refreshStatus();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally { setBusy(null); }
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

  return (
    <Layout>
      <div className="space-y-6">
        {/* Breadcrumb + state pill */}
        <Glass as="section" className="p-5 flex flex-wrap items-center gap-3 justify-between">
          <div className="flex flex-wrap items-baseline gap-2">
            <Link to="/" className="muted hover:text-ink text-sm">Projects</Link>
            <span className="muted">/</span>
            {runId && (
              <Link to={`/runs/${runId}`} className="muted hover:text-ink text-sm">
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
            <GlassPill className={`px-3 py-0.5 text-[10px] kicker ${status?.canonical_ready ? "text-emerald-300" : "text-warn"}`} title="Canonical means every built item has a live Wikibase read-back">
              {status ? `canonical ${status.canonical_live_count}/${status.canonical_ready ? status.canonical_live_count : status.canonical_live_count}` : "canonical loading…"}
            </GlassPill>
          </div>
        </Glass>

        {error && (
          <Glass as="p" variant="compact" className="p-3 text-sm text-danger">{error}</Glass>
        )}

        {/* Corpus RDF graph overview — same stats as the RDF Graph tab */}
        {runId && <GraphOverviewSummary runId={runId} />}

        {/* Ontology schema bootstrap (global, shared across every run) */}
        <SchemaBootstrapPanel runId={runId} />

        {/* Coverage */}
        <Glass as="section" className="p-6 space-y-3">
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
              <Link to={`/runs/${runId ?? ""}`} className="text-biu-sky hover:underline">
                Run page
              </Link>{" "}
              before viewing coverage.
            </p>
          )}

          {coverage && (
            <CoverageTable report={coverage} />
          )}
        </Glass>

        {/* Manifests */}
        <Glass as="section" className="p-6 space-y-3">
          <div>
            <div className="kicker">IIIF manifests</div>
            <h3 className="text-lg font-medium">
              {status?.manifest_count ?? 0} manifest
              {(status?.manifest_count ?? 0) === 1 ? "" : "s"} generated
            </h3>
            <p className="muted text-sm leading-relaxed mt-1">
              Each manuscript produces one IIIF Presentation API 3.0 manifest
              carrying the HMO scholarly overlay (folio-granular Codicological
              Units → Ranges, scribal interventions → AnnotationCollections,
              <code className="text-xs"> seeAlso</code> to the HMO graph node).
              Manifests sit under
              <code className="text-xs"> IIIF:MS_&lt;shelfmark&gt;/manifest.json</code>
              {" "}on the project Wikibase Cloud and are referenced via
              <code className="text-xs"> P6108</code> on the Wikidata item.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-3 pt-2">
            <button onClick={doBuild}
                    disabled={busy !== null || !status?.rdf_present}
                    className="button-primary text-sm">
              {busy === "build" ? "Building…" : "Build manifests"}
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
          </div>

          {build && (
            <p className="text-xs muted pt-1">
              Last build: {build.manifest_count} manifests · {build.total_canvases} canvases ·
              {" "}{build.total_ranges} CU ranges · {build.total_annotations} annotations
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

        {/* Wikibase items (build, upload, review on one page) */}
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

        {/* MARC record editor */}
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

        {/* Wikibase Cloud server config */}
        <Glass as="section" className="p-6 space-y-2">
          <div className="kicker">Wikibase Cloud</div>
          <h3 className="text-lg font-medium">Live-upload prerequisite</h3>
          <p className="muted text-sm leading-relaxed">
            Live writes to <code className="text-xs">mhm-hmo.wikibase.cloud</code>
            {" "}use server-held OAuth credentials configured by the deployment
            admin. Dry-run previews work without them.
          </p>
          <GlassPill
            className={`inline-block px-3 py-0.5 text-[10px] kicker ${
              wikibaseConfigured ? "text-biu-sky" : "text-warn"
            }`}
          >
            {wikibaseConfigured ? "✓ server configured" : "⚠ not configured — contact admin"}
          </GlassPill>
        </Glass>
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


// ── CoverageTable ────────────────────────────────────────────────────────


function CoverageTable({ report }: { report: HmoCoverageReport }) {
  const [statusFilter, setStatusFilter] = useState<string>("all");

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
              <tr key={row.class_uri} className="border-t border-white/5">
                <td className="px-3 py-2">
                  <span className="font-mono text-xs">{row.class_local_name}</span>
                  {row.class_label && row.class_label !== row.class_local_name && (
                    <span className="muted text-xs"> · {row.class_label}</span>
                  )}
                </td>
                <td className="px-3 py-2">
                  <ProjectionPill status={row.projection_status} />
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs">
                  {row.hmo_node_count}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs">
                  {row.projected_item_count}
                </td>
                <td className="px-3 py-2 text-xs muted">
                  {row.wikidata_properties.length > 0
                    ? row.wikidata_properties.join(" · ")
                    : "—"}
                </td>
              </tr>
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
    </div>
  );
}


function ProjectionPill({ status }: { status: string }) {
  const tone =
    status === "direct_wikidata_item"   ? "text-biu-sky" :
    status === "summarized_in_wikidata" ? "text-warn" :
    status === "hmo_or_wikibase_only"   ? "muted" :
                                          "text-danger";
  const label =
    status === "direct_wikidata_item"   ? "direct" :
    status === "summarized_in_wikidata" ? "summarised" :
    status === "hmo_or_wikibase_only"   ? "HMO-only" :
                                          "unknown";
  return (
    <GlassPill className={`px-2 py-0.5 text-[10px] kicker ${tone}`}>
      {label}
    </GlassPill>
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
