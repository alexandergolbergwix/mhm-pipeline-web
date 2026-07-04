/**
 * GraphOverviewSummary — compact "what's actually in this run's RDF
 * graph" panel: triples/manuscripts/nodes/edges counts plus node-type
 * and edge-type facet chips, with a link into the full RDF Graph
 * explorer for deep browsing.
 *
 * Mirrors the header stats + legend already shown on the RDF Graph
 * tab (StageRdf.tsx) so other Studio pages (HMO Wikibase Studio,
 * Wikidata Studio) can surface the same corpus context without
 * re-fetching/re-rendering the full graph canvas.
 */

import {useEffect, useState} from "react";
import {Link} from "react-router-dom";

import {Rdf, type GraphCatalogResponse, type RdfStatus} from "@/api/rdf";
import {Glass, GlassPill} from "@/components/glass";

export interface GraphOverviewSummaryProps {
  runId: string;
  /** Cap how many edge-predicate chips render before "+N more". */
  maxEdgeTypes?: number;
}

export function GraphOverviewSummary({runId, maxEdgeTypes = 10}: GraphOverviewSummaryProps) {
  const [status, setStatus] = useState<RdfStatus | null>(null);
  const [catalog, setCatalog] = useState<GraphCatalogResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Rdf.status(runId)
      .then((s) => {
        if (cancelled) return;
        setStatus(s);
        if (s.status === "idle") return null;
        return Rdf.catalog(runId).then((c) => { if (!cancelled) setCatalog(c); });
      })
      .catch(() => { if (!cancelled) setError("Could not load the RDF graph overview for this run."); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [runId]);

  return (
    <Glass as="section" className="p-6 space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <div className="kicker">Corpus RDF graph</div>
          <h3 className="text-lg font-medium">
            {catalog
              ? `${catalog.total_nodes.toLocaleString()} nodes · ${catalog.total_edges.toLocaleString()} edges`
              : "Graph overview"}
          </h3>
        </div>
        <Link to={`/runs/${runId}/rdf`} className="text-biu-sky hover:underline text-sm">
          Open full RDF Graph explorer →
        </Link>
      </div>

      {loading && <p className="muted text-sm">Loading…</p>}
      {error && <p className="text-sm text-danger">{error}</p>}

      {!loading && !error && status?.status === "idle" && (
        <p className="muted text-sm">
          No RDF graph has been built for this run yet — build it in the{" "}
          <Link to={`/runs/${runId}/rdf`} className="text-biu-sky hover:underline">RDF Graph</Link> tab
          to see corpus stats here.
        </p>
      )}

      {!loading && !error && status && status.status !== "idle" && (
        <>
          <p className="text-sm muted">
            {status.triples_count != null && <>{status.triples_count.toLocaleString()} triples</>}
            {status.manuscripts_count != null && (
              <> · {status.manuscripts_count.toLocaleString()} manuscript
                {status.manuscripts_count === 1 ? "" : "s"}</>
            )}
            {catalog && (
              <> · {catalog.total_nodes.toLocaleString()} nodes · {catalog.total_edges.toLocaleString()} edges</>
            )}
          </p>
          <p className="text-xs muted">
            Built from approved AI extraction + authority enrichment data
          </p>

          {catalog && <FacetChips title="Node type" counts={catalog.node_types} />}
          {catalog && (
            <FacetChips
              title="Edge type"
              counts={catalog.edge_predicates}
              max={maxEdgeTypes}
            />
          )}
        </>
      )}
    </Glass>
  );
}

function FacetChips({
  title, counts, max,
}: {
  title: string;
  counts: Record<string, number>;
  max?: number;
}) {
  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (sorted.length === 0) return null;
  const shown = max ? sorted.slice(0, max) : sorted;
  const hiddenCount = sorted.length - shown.length;

  return (
    <div className="space-y-1">
      <div className="kicker">{title}</div>
      <div className="flex flex-wrap gap-1.5">
        {shown.map(([label, count]) => (
          <GlassPill key={label} className="px-2 py-0.5 text-[10px]">
            {label} <span className="muted">({count.toLocaleString()})</span>
          </GlassPill>
        ))}
        {hiddenCount > 0 && (
          <GlassPill className="px-2 py-0.5 text-[10px] muted">+{hiddenCount} more</GlassPill>
        )}
      </div>
    </div>
  );
}
