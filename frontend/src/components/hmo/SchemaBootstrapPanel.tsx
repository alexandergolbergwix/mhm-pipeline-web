import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/api/client";
import {
  HmoWikibaseSchema,
  type HmoSchemaBootstrapResult,
  type HmoSchemaStatus,
} from "@/api/hmoWikibaseSchema";
import { Glass, GlassPill } from "@/components/glass";

/**
 * Global (not per-run) schema bootstrap panel: creates every missing
 * HMO ontology class/property on mhm-hmo.wikibase.cloud. Lives above
 * the per-run panels in HmoStudio.tsx since the schema is shared
 * across every run (Phase 3, dev-docs/hmo-wikibase-studio-plan.md).
 */
export function SchemaBootstrapPanel() {
  const [status, setStatus] = useState<HmoSchemaStatus | null>(null);
  const [result, setResult] = useState<HmoSchemaBootstrapResult | null>(null);
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

  async function doBootstrap() {
    setBusy(true);
    setError(null);
    try {
      const r = await HmoWikibaseSchema.bootstrap(dryRun);
      setResult(r);
      await refresh();
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
            Idempotent: re-running only creates what&apos;s missing.
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

      <div className="flex flex-wrap items-center gap-3 pt-1">
        <label className="flex items-center gap-1 text-sm muted">
          <input
            type="checkbox"
            checked={dryRun}
            onChange={(e) => setDryRun(e.target.checked)}
            disabled={busy}
          />
          Dry run
        </label>
        <button
          onClick={doBootstrap}
          disabled={busy || (!dryRun && !credsReady)}
          className={dryRun ? "button-ghost text-sm" : "button-primary text-sm"}
        >
          {busy
            ? dryRun
              ? "Previewing…"
              : "Bootstrapping…"
            : dryRun
              ? "Preview bootstrap"
              : "Run schema bootstrap"}
        </button>
        {!dryRun && !credsReady && (
          <span className="text-xs muted">
            Add Wikibase bot credentials in Settings first.
          </span>
        )}
      </div>

      {result && <BootstrapResultSummary result={result} />}
    </Glass>
  );
}

function BootstrapResultSummary({ result }: { result: HmoSchemaBootstrapResult }) {
  const [expand, setExpand] = useState(false);
  return (
    <div className="border-t border-white/5 pt-3 space-y-2">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <p className="text-sm">
          <span className="muted">{result.dry_run ? "Would create:" : "Created:"}</span>{" "}
          <b className="text-biu-sky">{result.created}</b>
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
