import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { ApiError } from "@/api/client";
import {
  HmoStudio,
  type HmoBuildResult,
  type HmoCoverageReport,
  type HmoStudioStatus,
  type HmoUploadResult,
} from "@/api/hmoStudio";


type Busy = null | "build" | "upload" | "coverage";


export default function HmoStudioRoute() {
  const { runId } = useParams<{ runId: string }>();

  const [status, setStatus] = useState<HmoStudioStatus | null>(null);
  const [coverage, setCoverage] = useState<HmoCoverageReport | null>(null);
  const [build, setBuild] = useState<HmoBuildResult | null>(null);
  const [upload, setUpload] = useState<HmoUploadResult | null>(null);

  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);
  const [dryRun, setDryRun] = useState(true);

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
    setBusy("coverage"); setError(null);
    try {
      setCoverage(await HmoStudio.coverage(runId));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally { setBusy(null); }
  }, [runId]);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  // Auto-load coverage when the RDF is present and we haven't loaded it yet.
  useEffect(() => {
    if (status?.rdf_present && coverage === null && busy !== "coverage") {
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
      case "built":    return { label: "built",    tone: "text-yellow-300" };
      case "error":    return { label: "error",    tone: "text-red-300" };
      case "idle":
      default:         return { label: "idle",     tone: "muted" };
    }
  }, [status]);

  const credsReady = !!status?.bot_username_set && !!status?.bot_password_set;

  return (
    <Layout>
      <div className="space-y-6">
        {/* Breadcrumb + state pill */}
        <section className="glass p-5 flex flex-wrap items-center gap-3 justify-between">
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
          <span className={`glass-pill px-3 py-0.5 text-[10px] kicker ${statePill.tone}`}>
            {statePill.label}
          </span>
        </section>

        {error && (
          <p className="glass p-3 text-sm text-red-300">{error}</p>
        )}

        {/* Coverage */}
        <section className="glass p-6 space-y-3">
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
        </section>

        {/* Manifests */}
        <section className="glass p-6 space-y-3">
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
                                              || (!dryRun && !credsReady)}
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
        </section>

        {/* Bot creds */}
        <section className="glass p-6 space-y-2">
          <div className="kicker">Wikibase Cloud bot credentials</div>
          <h3 className="text-lg font-medium">Live-upload prerequisite</h3>
          <p className="muted text-sm leading-relaxed">
            Live writes to <code className="text-xs">mhm-hmo.wikibase.cloud</code>
            {" "}require a bot username + bot password (issued at{" "}
            <a target="_blank" rel="noopener"
               href="https://mhm-hmo.wikibase.cloud/wiki/Special:BotPasswords"
               className="text-biu-sky hover:underline">
              Special:BotPasswords
            </a>). Dry-run previews work without credentials.
          </p>
          <div className="flex flex-wrap gap-2 pt-1 text-xs">
            <CredBadge ok={!!status?.bot_username_set} label="bot username" />
            <CredBadge ok={!!status?.bot_password_set} label="bot password" />
            <Link to="/settings" className="button-ghost text-xs">
              Open Settings → Credentials
            </Link>
          </div>
        </section>
      </div>
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

      <div className="overflow-x-auto border border-white/5 rounded-lg">
        <table className="w-full text-sm">
          <thead className="bg-white/3 text-xs uppercase muted tracking-wider">
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
      </div>
    </div>
  );
}


function ProjectionPill({ status }: { status: string }) {
  const tone =
    status === "direct_wikidata_item"   ? "text-biu-sky" :
    status === "summarized_in_wikidata" ? "text-yellow-300" :
    status === "hmo_or_wikibase_only"   ? "muted" :
                                          "text-red-300";
  const label =
    status === "direct_wikidata_item"   ? "direct" :
    status === "summarized_in_wikidata" ? "summarised" :
    status === "hmo_or_wikibase_only"   ? "HMO-only" :
                                          "unknown";
  return (
    <span className={`glass-pill px-2 py-0.5 text-[10px] kicker ${tone}`}>
      {label}
    </span>
  );
}


function CredBadge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className={`glass-pill px-2 py-0.5 text-[10px] kicker ${
      ok ? "text-biu-sky" : "text-red-300"
    }`}>
      {ok ? "✓" : "⚠"} {label}
    </span>
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
          {report.failed > 0 && <> · <span className="text-red-300">{report.failed} failed</span></>}
          {cachedAt && <> · <span className="muted">{cachedAt}</span></>}
        </p>
        <button onClick={() => setExpand((v) => !v)} className="button-ghost text-xs">
          {expand ? "Hide details" : "Show details"}
        </button>
      </div>

      {expand && (
        <div className="overflow-x-auto border border-white/5 rounded-lg">
          <table className="w-full text-sm">
            <thead className="bg-white/3 text-xs uppercase muted tracking-wider">
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
        </div>
      )}
    </div>
  );
}


function UploadStatusPill({ status }: { status: string }) {
  const tone =
    status === "created" || status === "updated" ? "text-biu-sky" :
    status === "dry_run"                          ? "text-yellow-300" :
    status === "unchanged"                        ? "muted" :
                                                    "text-red-300";
  return (
    <span className={`glass-pill px-2 py-0.5 text-[10px] kicker ${tone}`}>
      {status}
    </span>
  );
}
