import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/api/client";
import type { AiVerdict } from "@/api/extractionApprovals";
import {
  HmoWikibaseSchema,
  isSchemaBootstrapJob,
  type HmoSchemaBootstrapResult,
  type HmoSchemaStatus,
} from "@/api/hmoWikibaseSchema";
import type { RunJobSnapshot } from "@/api/runJobs";
import { HmoSchemaVerificationModal } from "@/components/hmo/HmoSchemaVerificationModal";
import { SchemaBootstrapDetails } from "@/components/hmo/SchemaBootstrapDetails";
import { Glass, GlassPill } from "@/components/glass";
import { useRunJobAttachment } from "@/hooks/useRunJobAttachment";

interface SchemaBootstrapPanelProps {
  runId?: string;
}

const VERIFIABLE_STATUSES = new Set([
  "would_create",
  "created",
  "failed",
  "skipped",
]);

function verifiableEntries(entries: HmoSchemaBootstrapResult["entries"]) {
  return entries.filter((e) => VERIFIABLE_STATUSES.has(e.status));
}

function bootstrapResultFromJob(job: RunJobSnapshot): HmoSchemaBootstrapResult | null {
  const raw = job.result;
  if (!raw || typeof raw !== "object") return null;
  const entries = (raw as {entries?: unknown}).entries;
  if (!Array.isArray(entries)) return null;
  return {
    dry_run: false,
    created: Number((raw as {created?: unknown}).created ?? 0),
    skipped: Number((raw as {skipped?: unknown}).skipped ?? 0),
    failed: Number((raw as {failed?: unknown}).failed ?? 0),
    would_create: Number((raw as {would_create?: unknown}).would_create ?? 0),
    entries: entries as HmoSchemaBootstrapResult["entries"],
  };
}

export function SchemaBootstrapPanel({ runId }: SchemaBootstrapPanelProps) {
  const [status, setStatus] = useState<HmoSchemaStatus | null>(null);
  const [result, setResult] = useState<HmoSchemaBootstrapResult | null>(null);
  const [job, setJob] = useState<RunJobSnapshot | null>(null);
  const [dryRun, setDryRun] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [verdicts, setVerdicts] = useState<Record<string, AiVerdict>>({});
  const [verifyOpen, setVerifyOpen] = useState(false);

  const refreshPreviewIfFullyMapped = useCallback(async (s: HmoSchemaStatus) => {
    const complete =
      s.mapped_classes === s.total_classes
      && s.mapped_properties === s.total_properties;
    if (!complete) return;
    try {
      const preview = await HmoWikibaseSchema.bootstrap(true, runId);
      if (!isSchemaBootstrapJob(preview)) {
        setResult(preview);
      }
    } catch {
      /* keep whatever last-report returned */
    }
  }, [runId]);

  const refresh = useCallback(async () => {
    try {
      const s = await HmoWikibaseSchema.status();
      setStatus(s);
      await refreshPreviewIfFullyMapped(s);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }, [refreshPreviewIfFullyMapped]);

  const loadLastReport = useCallback(async () => {
    try {
      const report = await HmoWikibaseSchema.lastReport();
      setResult(report);
    } catch {
      /* non-fatal — panel still works without a cached report */
    }
  }, []);

  const initPanel = useCallback(async () => {
    try {
      const s = await HmoWikibaseSchema.status();
      setStatus(s);
      const complete =
        s.mapped_classes === s.total_classes
        && s.mapped_properties === s.total_properties;
      if (complete) {
        await refreshPreviewIfFullyMapped(s);
      } else {
        await loadLastReport();
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }, [loadLastReport, refreshPreviewIfFullyMapped]);

  useEffect(() => {
    void initPanel();
  }, [initPanel]);

  const { activeJob, setTrackedJobId, ensureJobPolling } = useRunJobAttachment(
    runId,
    "hmo_schema_bootstrap",
    (j) => {
      setJob(j);
      if (j.status === "succeeded") {
        const fromJob = bootstrapResultFromJob(j);
        if (fromJob) setResult(fromJob);
        void refresh();
        void loadLastReport();
      }
      if (j.status === "failed" || j.status === "cancelled") {
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

  const wikibaseConfigured = !!status?.wikibase_configured;
  const fullyMapped =
    !!status &&
    status.mapped_classes === status.total_classes &&
    status.mapped_properties === status.total_properties;
  const jobRunning = liveJob?.status === "queued" || liveJob?.status === "running";
  const displayResult =
    result ?? (liveJob?.status === "succeeded" ? bootstrapResultFromJob(liveJob) : null);

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
            <code className="text-xs">mhm-hmo.wikibase.cloud</code>. Preview
            with a dry run, verify entries with AI, then run a live bootstrap.
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

      {status && !wikibaseConfigured && (
        <p className="text-xs text-warn">
          Wikibase Cloud is not configured on this server — contact an admin to
          enable live bootstrap (dry-run preview works without it).
        </p>
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
          disabled={busy || jobRunning || (!dryRun && (!wikibaseConfigured || !runId))}
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
        {displayResult && runId && (
          <button
            type="button"
            onClick={() => setVerifyOpen(true)}
            className="button-ghost text-sm"
            disabled={jobRunning}
          >
            Verify with AI
          </button>
        )}
        {!dryRun && wikibaseConfigured && !runId && (
          <span className="text-xs muted">
            Open this panel from a run to start a live bootstrap.
          </span>
        )}
      </div>

      {liveJob && <BootstrapJobProgress job={liveJob} />}

      {displayResult && (
        <SchemaBootstrapDetails
          result={displayResult}
          defaultExpanded
          verdicts={verdicts}
        />
      )}

      {verifyOpen && runId && displayResult && (
        <HmoSchemaVerificationModal
          runId={runId}
          scopeLabel={`${verifiableEntries(displayResult.entries).length} schema entries`}
          ontologyUris={verifiableEntries(displayResult.entries).map((e) => e.ontology_uri)}
          onClose={() => setVerifyOpen(false)}
          onVerdictsLanded={(next) => {
            if (Object.keys(next).length > 0) {
              setVerdicts((prev) => ({...prev, ...next}));
            }
          }}
        />
      )}
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
    </div>
  );
}
