import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "@/api/client";
import {
  HmoWikibaseSchema,
  isSchemaBootstrapJob,
  type HmoSchemaBootstrapResult,
  type HmoSchemaStatus,
} from "@/api/hmoWikibaseSchema";
import type { RunJobSnapshot } from "@/api/runJobs";
import { Glass, GlassPill } from "@/components/glass";
import { useRunJobAttachment } from "@/hooks/useRunJobAttachment";

interface SchemaBootstrapPanelProps {
  /** Anchors the background job for a live bootstrap (dry-run doesn't need one). */
  runId?: string;
}

/**
 * Global (not per-run) schema bootstrap panel: creates every missing
 * HMO ontology class/property on mhm-hmo.wikibase.cloud. Lives above
 * the per-run panels in HmoStudio.tsx since the schema is shared
 * across every run (Phase 3, dev-docs/hmo-wikibase-studio-plan.md).
 *
 * A live bootstrap makes ~380 sequential external calls — too slow for one
 * HTTP request — so the backend runs it as a `run_jobs` background job and
 * this panel tracks its progress via `useRunJobAttachment` (poll + live
 * WebSocket push), rendering a progress bar that survives tab navigation.
 */
export function SchemaBootstrapPanel({ runId }: SchemaBootstrapPanelProps) {
  const [status, setStatus] = useState<HmoSchemaStatus | null>(null);
  const [result, setResult] = useState<HmoSchemaBootstrapResult | null>(null);
  const [job, setJob] = useState<RunJobSnapshot | null>(null);
  const [dryRun, setDryRun] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await HmoWikibaseSchema.status());
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const { activeJob, setTrackedJobId, ensureJobPolling } = useRunJobAttachment(
    runId,
    "hmo_schema_bootstrap",
    (j) => {
      setJob(j);
      if (j.status === "succeeded" || j.status === "failed" || j.status === "cancelled") {
        void refresh();
      }
    },
  );

  const liveJob = activeJob ?? job;

  async function doBootstrap() {
    setBusy(true);
    setError(null);
    try {
      const r = await HmoWikibaseSchema.bootstrap(dryRun, runId);
      if (isSchemaBootstrapJob(r)) {
        setJob(r);
        setTrackedJobId(r.id);
        ensureJobPolling();
      } else {
        setResult(r);
        await refresh();
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  const credsReady = !!status?.bot_username_set && !!status?.bot_password_set;
  const fullyMapped =
    !!status &&
    status.mapped_classes === status.total_classes &&
    status.mapped_properties === status.total_properties;
  const jobRunning = liveJob?.status === "queued" || liveJob?.status === "running";

  return (
    <Glass as="section" className="p-6 space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <div className="kicker">HMO ontology schema (global)</div>
          <h3 className="text-lg font-medium">
            {status
              ? `${status.mapped_classes}/${status.total_classes} classes · ` +
                `${status.mapped_properties}/${status.total_properties} properties mapped`
              : "Schema bootstrap"}
          </h3>
          <p className="muted text-sm leading-relaxed mt-1">
            Creates every class/property from the ontology as a real
            Wikibase Item/Property on{" "}
            <code className="text-xs">mhm-hmo.wikibase.cloud</code>. One
            ontology, one Wikibase instance — shared by every run.
            Idempotent: re-running only creates what&apos;s missing. A live
            run happens in the background — you can navigate away and come
            back without losing progress.
          </p>
        </div>
        {status && (
          <GlassPill
            className={`px-3 py-0.5 text-[10px] kicker ${fullyMapped ? "text-biu-sky" : "text-warn"}`}
          >
            {fullyMapped ? "fully mapped" : "incomplete"}
          </GlassPill>
        )}
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}

      {status && status.missing_sample.length > 0 && (
        <div className="text-xs muted">
          Missing (sample): {status.missing_sample.slice(0, 5).join(", ")}
          {status.missing_sample.length > 5 && "…"}
        </div>
      )}

      {status && !credsReady && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-warn">
            Add Wikibase bot credentials in Settings to run a live bootstrap
            (previewing works without them):
          </span>
          <CredBadge ok={status.bot_username_set} label="bot username" />
          <CredBadge ok={status.bot_password_set} label="bot password" />
          <Link to="/settings" className="button-ghost text-xs">
            Open Settings → Credentials
          </Link>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 pt-1">
        <label className="flex items-center gap-1 text-sm muted">
          <input
            type="checkbox"
            checked={dryRun}
            onChange={(e) => setDryRun(e.target.checked)}
            disabled={busy || jobRunning}
          />
          Dry run
        </label>
        <button
          onClick={doBootstrap}
          disabled={busy || jobRunning || (!dryRun && (!credsReady || !runId))}
          className={dryRun ? "button-ghost text-sm" : "button-primary text-sm"}
        >
          {busy || jobRunning
            ? dryRun
              ? "Previewing…"
              : "Bootstrapping…"
            : dryRun
              ? "Preview bootstrap"
              : "Run schema bootstrap"}
        </button>
        {!dryRun && credsReady && !runId && (
          <span className="text-xs muted">
            Open this panel from a run to start a live bootstrap.
          </span>
        )}
      </div>

      {liveJob && <BootstrapJobProgress job={liveJob} />}

      {result && <BootstrapResultSummary result={result} />}
    </Glass>
  );
}

function BootstrapJobProgress({ job }: { job: RunJobSnapshot }) {
  const { processed, total, message } = job.progress;
  const pct = total && total > 0 ? Math.round(((processed ?? 0) / total) * 100) : 0;
  const done = job.status === "succeeded" || job.status === "failed" || job.status === "cancelled";

  return (
    <div className="border-t border-white/5 pt-3 space-y-2">
      <div className="flex items-baseline justify-between flex-wrap gap-2 text-sm">
        <p>
          <span className="muted">
            {job.status === "succeeded" ? "Bootstrap complete:" :
             job.status === "failed" ? "Bootstrap failed:" :
             job.status === "cancelled" ? "Bootstrap cancelled:" :
             "Bootstrapping…"}
          </span>{" "}
          {message && <span className="text-ink">{message}</span>}
        </p>
        {!done && total ? (
          <span className="muted text-xs">{processed ?? 0} / {total}</span>
        ) : null}
      </div>
      {!done && (
        <div className="h-1.5 w-full rounded-full bg-white/8 overflow-hidden">
          <div
            className="h-full bg-biu-sky transition-[width] duration-300"
            style={{ width: `${Math.min(100, Math.max(pct, total ? 2 : 100))}%` }}
          />
        </div>
      )}
      {job.status === "failed" && job.error && (
        <p className="text-xs text-danger">{job.error}</p>
      )}
      {job.status === "succeeded" && job.result && (
        <p className="text-xs muted">
          created {String(job.result.created ?? 0)} · skipped {String(job.result.skipped ?? 0)}
          {Number(job.result.failed ?? 0) > 0 && (
            <> · <span className="text-danger">failed {String(job.result.failed)}</span></>
          )}
        </p>
      )}
    </div>
  );
}

function BootstrapResultSummary({ result }: { result: HmoSchemaBootstrapResult }) {
  const [expand, setExpand] = useState(false);
  return (
    <div className="border-t border-white/5 pt-3 space-y-2">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <p className="text-sm">
          <span className="muted">{result.dry_run ? "Would create:" : "Created:"}</span>{" "}
          <b className="text-biu-sky">{result.dry_run ? result.would_create : result.created}</b>
          {" · "}
          <span className="muted">skipped {result.skipped}</span>
          {result.failed > 0 && (
            <>
              {" · "}
              <span className="text-danger">failed {result.failed}</span>
            </>
          )}
        </p>
        <button onClick={() => setExpand((v) => !v)} className="button-ghost text-xs">
          {expand ? "Hide details" : "Show details"}
        </button>
      </div>
      {expand && (
        <div className="overflow-x-auto border border-white/5 rounded-lg max-h-72 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="bg-white/3 text-xs uppercase muted tracking-wider sticky top-0">
              <tr>
                <th className="text-left px-3 py-2">Kind</th>
                <th className="text-left px-3 py-2">Label</th>
                <th className="text-left px-3 py-2">Status</th>
                <th className="text-left px-3 py-2">Wikibase id</th>
              </tr>
            </thead>
            <tbody>
              {result.entries.map((entry) => (
                <tr key={entry.ontology_uri} className="border-t border-white/5">
                  <td className="px-3 py-2 text-xs muted">{entry.entity_kind}</td>
                  <td className="px-3 py-2 text-xs">{entry.label}</td>
                  <td className="px-3 py-2">
                    <EntryStatusPill status={entry.status} />
                  </td>
                  <td className="px-3 py-2 text-xs font-mono">{entry.wikibase_id ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function CredBadge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <GlassPill className={`px-2 py-0.5 text-[10px] kicker ${
      ok ? "text-biu-sky" : "text-danger"
    }`}>
      {ok ? "✓" : "⚠"} {label}
    </GlassPill>
  );
}

function EntryStatusPill({ status }: { status: string }) {
  const tone =
    status === "created"
      ? "text-biu-sky"
      : status === "would_create"
        ? "text-warn"
        : status === "skipped"
          ? "muted"
          : "text-danger";
  return <GlassPill className={`px-2 py-0.5 text-[10px] kicker ${tone}`}>{status}</GlassPill>;
}
