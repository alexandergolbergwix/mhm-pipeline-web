import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/api/client";
import { HmoStudio, type HmoItemStatus, type HmoItemUploadResult } from "@/api/hmoStudio";
import { Glass, GlassPill } from "@/components/glass";

interface ItemUploadPanelProps {
  runId: string;
  credsReady: boolean;
  /** Bump this to force a status refresh after a sibling ItemBuildPanel builds. */
  refreshToken?: unknown;
}

/**
 * Phase 5: create-only, two-pass upload of the run's most recent item
 * build. Disabled until a build exists.
 */
export function ItemUploadPanel({ runId, credsReady, refreshToken }: ItemUploadPanelProps) {
  const [status, setStatus] = useState<HmoItemStatus | null>(null);
  const [result, setResult] = useState<HmoItemUploadResult | null>(null);
  const [dryRun, setDryRun] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await HmoStudio.itemStatus(runId));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }, [runId]);

  useEffect(() => {
    void refresh();
  }, [refresh, refreshToken]);

  async function doUpload() {
    setBusy(true);
    setError(null);
    try {
      const r = await HmoStudio.uploadItems(runId, dryRun);
      setResult(r);
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  const canUpload = !!status?.build_present;

  return (
    <Glass as="section" className="p-6 space-y-3">
      <div>
        <div className="kicker">Upload to Wikibase Cloud</div>
        <h3 className="text-lg font-medium">Create-only, two-pass upload</h3>
        <p className="muted text-sm leading-relaxed mt-1">
          Pass 1 creates every not-yet-uploaded item with its literal
          claims. Pass 2 links item-to-item claims once both ends have
          real Wikibase ids. Already-uploaded items are skipped, never
          edited.
        </p>
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}

      {!canUpload && (
        <p className="text-sm muted">Build items above before uploading.</p>
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
          onClick={doUpload}
          disabled={busy || !canUpload || (!dryRun && !credsReady)}
          className={dryRun ? "button-ghost text-sm" : "button-primary text-sm"}
        >
          {busy
            ? dryRun
              ? "Previewing…"
              : "Uploading…"
            : dryRun
              ? "Preview upload"
              : "Upload items"}
        </button>
        {!dryRun && !credsReady && (
          <span className="text-xs muted">
            Add Wikibase bot credentials in Settings first.
          </span>
        )}
      </div>

      {result && <UploadResultSummary result={result} />}
    </Glass>
  );
}

function UploadResultSummary({ result }: { result: HmoItemUploadResult }) {
  const [expand, setExpand] = useState(false);
  return (
    <div className="border-t border-white/5 pt-3 space-y-2">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <p className="text-sm">
          <span className="muted">{result.dry_run ? "Would create:" : "Created:"}</span>{" "}
          <b className="text-biu-sky">{result.created}</b>
          {" · linked "}
          <b className="text-biu-sky">{result.linked}</b>
          {" · skipped "}
          {result.skipped}
          {result.unresolved_links > 0 && (
            <>
              {" · "}
              <span className="text-warn">unresolved links {result.unresolved_links}</span>
            </>
          )}
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
        <div className="space-y-3">
          <OutcomeTable
            title="Items"
            rows={result.outcomes.map((o) => ({
              key: o.local_id,
              cols: [o.local_id, o.status, o.wikibase_id ?? "—", o.message],
            }))}
            headers={["Local id", "Status", "Wikibase id", "Message"]}
            pillIndex={1}
          />
          {result.link_outcomes.length > 0 && (
            <OutcomeTable
              title="Deferred links"
              rows={result.link_outcomes.map((o, i) => ({
                key: `${o.source_local_id}-${o.property_id}-${i}`,
                cols: [o.source_local_id, o.property_id, o.target_local_id, o.status],
              }))}
              headers={["Source", "Property", "Target", "Status"]}
              pillIndex={3}
            />
          )}
        </div>
      )}
    </div>
  );
}

function OutcomeTable({
  title,
  headers,
  rows,
  pillIndex,
}: {
  title: string;
  headers: string[];
  rows: { key: string; cols: string[] }[];
  pillIndex: number;
}) {
  return (
    <div>
      <div className="text-xs muted mb-1">{title}</div>
      <div className="overflow-x-auto border border-white/5 rounded-lg max-h-56 overflow-y-auto">
        <table className="w-full text-sm">
          <thead className="bg-white/3 text-xs uppercase muted tracking-wider sticky top-0">
            <tr>
              {headers.map((h) => (
                <th key={h} className="text-left px-3 py-2">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} className="border-t border-white/5">
                {row.cols.map((c, i) => (
                  <td
                    key={i}
                    className={`px-3 py-2 text-xs ${i === 0 ? "font-mono" : ""}`}
                  >
                    {i === pillIndex ? <GlassPill className="px-2 py-0.5 text-[10px] kicker">{c}</GlassPill> : c}
                  </td>
                ))}
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={headers.length} className="px-3 py-4 text-center muted text-sm">
                  None.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
