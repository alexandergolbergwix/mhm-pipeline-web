import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/api/client";
import { HmoStudio, type HmoItemBuildResult, type HmoItemStatus } from "@/api/hmoStudio";
import { Glass, GlassPill } from "@/components/glass";

interface ItemBuildPanelProps {
  runId: string;
  rdfPresent: boolean;
  /** Called after a successful build so a sibling ItemUploadPanel can refresh. */
  onBuilt?: () => void;
  /** Toolbar-only row for the review panel header. */
  compact?: boolean;
}

/**
 * Phase 4: resolves the run's RDF instances against the live schema
 * mapping into real-PID/QID-shaped item drafts, cached per-run.
 */
export function ItemBuildPanel({runId, rdfPresent, onBuilt, compact = false}: ItemBuildPanelProps) {
  const [status, setStatus] = useState<HmoItemStatus | null>(null);
  const [result, setResult] = useState<HmoItemBuildResult | null>(null);
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
  }, [refresh]);

  async function doBuild(forceRebuild: boolean) {
    setBusy(true);
    setError(null);
    try {
      const r = await HmoStudio.buildItems(runId, forceRebuild);
      setResult(r);
      await refresh();
      onBuilt?.();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  const actions = (
    <div className="flex flex-wrap items-center gap-3">
      <button
        onClick={() => void doBuild(false)}
        disabled={busy || !rdfPresent}
        className="button-primary text-sm"
        data-testid="hmo-build-items"
      >
        {busy ? "Building…" : "Build items"}
      </button>
      {status?.build_present && (
        <button
          onClick={() => void doBuild(true)}
          disabled={busy || !rdfPresent}
          className="button-ghost text-sm"
          data-testid="hmo-rebuild-skip-cache"
          title="Bypass the cached item build and resolve fresh from the RDF TTL"
        >
          {busy ? "Rebuilding…" : "Rebuild (skip cache)"}
        </button>
      )}
      {!rdfPresent && (
        <span className="text-xs muted">Build the RDF graph first.</span>
      )}
      {status?.build_present && (
        <GlassPill className="px-3 py-0.5 text-[10px] kicker text-biu-sky">
          {status.uploaded_count}/{status.entity_count} on wiki
        </GlassPill>
      )}
    </div>
  );

  if (compact) {
    return (
      <div className="space-y-2" data-testid="hmo-item-build-actions">
        {error && <p className="text-sm text-danger">{error}</p>}
        {actions}
        {result && (
          <p className="text-xs muted">
            {result.from_cache ? "Cached build" : "Fresh build"}: {result.entity_count} entities
          </p>
        )}
      </div>
    );
  }

  return (
    <Glass as="section" className="p-6 space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <div className="kicker">Wikibase items (this run)</div>
          <h3 className="text-lg font-medium">
            {status?.build_present
              ? `${status.entity_count} entities · ${status.deferred_link_count} deferred links`
              : "No build yet"}
          </h3>
          <p className="muted text-sm leading-relaxed mt-1">
            Resolves manuscripts, persons, places, etc. from this run&apos;s
            RDF graph against the live ontology schema. Requires the
            schema bootstrap above to have created every class/property
            the graph references.
          </p>
        </div>
        {status?.build_present && (
          <GlassPill className="px-3 py-0.5 text-[10px] kicker text-biu-sky">
            {status.uploaded_count}/{status.entity_count} uploaded
          </GlassPill>
        )}
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}
      {actions}

      {result && (
        <p className="text-xs muted pt-1">
          {result.from_cache ? "Cached build" : "Fresh build"}: {result.entity_count} entities ·{" "}
          {result.deferred_link_count} deferred item links
          {result.skipped_statement_count > 0 && (
            <> · {result.skipped_statement_count} skipped statements</>
          )}
        </p>
      )}
    </Glass>
  );
}
