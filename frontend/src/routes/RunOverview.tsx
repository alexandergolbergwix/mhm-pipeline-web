/**
 * Run overview — landing page for one run.
 *
 * Five stage tiles in a 2×3 grid (last row half-empty). Each tile
 * fetches its own status in parallel on mount; failures are surfaced
 * as a per-tile muted message rather than crashing the whole page.
 *
 * Tiles:
 *   1. Stage 2 · AI Extraction      → /runs/:id/extraction
 *   2. Stage 3 · Authority          → /runs/:id   (legacy RunDetail)
 *   3. Stage 4 · RDF Graph          → /runs/:id/rdf
 *   4. HMO Wikibase Studio          → /runs/:id/hmo-studio  (not wired yet)
 *   5. Wikidata Studio              → /runs/:id/wikidata-studio
 */

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { ApiError } from "@/api/client";
import { Extraction, type ExtractionStatus } from "@/api/extraction";
import { Rdf, type RdfStatus } from "@/api/rdf";
import { Runs, type RunDetail } from "@/api/runs";
import { Studio, type StudioBuild } from "@/api/wikidataStudio";


export default function RunOverview() {
  const { runId } = useParams<{ runId: string }>();

  const [run, setRun]           = useState<RunDetail | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const [extraction, setExtraction]       = useState<TileState<ExtractionStatus>>({ status: "loading" });
  const [rdf, setRdf]                     = useState<TileState<RdfStatus>>({ status: "loading" });
  const [studio, setStudio]               = useState<TileState<StudioBuild>>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    async function fetchAll() {
      if (!runId) return;
      try {
        setRun(await Runs.get(runId));
      } catch (e) {
        if (cancelled) return;
        setRunError(e instanceof ApiError ? e.detail : String(e));
      }

      // Fire all four stage-status calls in parallel. We deliberately
      // settle independently — one slow endpoint shouldn't block the
      // rest of the grid.
      Extraction.status(runId).then(
        (s) => !cancelled && setExtraction({ status: "ok", data: s }),
        (e) => !cancelled && setExtraction({ status: "error", error: errMessage(e) }),
      );
      Rdf.status(runId).then(
        (s) => !cancelled && setRdf({ status: "ok", data: s }),
        (e) => !cancelled && setRdf({ status: "error", error: errMessage(e) }),
      );
      Studio.build(runId, /* approvedOnly */ false).then(
        (s) => !cancelled && setStudio({ status: "ok", data: s }),
        (e) => !cancelled && setStudio({ status: "error", error: errMessage(e) }),
      );
    }
    void fetchAll();
    return () => { cancelled = true; };
  }, [runId]);

  const authorityCounts = useMemo(() => {
    if (!run) return { total: 0, approved: 0 };
    return {
      total:    run.matches.length,
      approved: run.matches.filter((m) => m.approved).length,
    };
  }, [run]);

  if (runError) {
    return (
      <Layout>
        <div className="glass p-6 text-red-300">{runError}</div>
      </Layout>
    );
  }
  if (!run) {
    return <Layout><p className="muted">Loading run…</p></Layout>;
  }

  return (
    <Layout>
      <div className="space-y-6">
        <section className="glass p-6 space-y-2">
          <div className="kicker">
            Run · <Link to={`/projects/${run.project_id}`} className="hover:text-ink underline">
              back to project
            </Link>
          </div>
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 className="text-2xl font-semibold">{run.name}</h2>
            <RunStatusPill status={run.status} />
          </div>
          <p className="muted text-sm">
            {run.record_count} record{run.record_count === 1 ? "" : "s"} ·{" "}
            {run.match_count} candidate match{run.match_count === 1 ? "" : "es"} ·{" "}
            {new Date(run.created_at).toLocaleString()}
          </p>
          {run.error && <p className="text-red-300 text-sm">{run.error}</p>}
        </section>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <StageTile
            to={`/runs/${runId}/extraction`}
            kicker="AI"
            title="AI Extraction"
            description="Hebrew Person NER · Provenance · Contents · Genre classifier"
            statePill={extractionPill(extraction)}
            stats={extractionStats(extraction)}
          />
          <StageTile
            to={`/runs/${runId}`}
            kicker="Auth"
            title="Authority"
            description="Mazal · VIAF · Wikidata · KIMA"
            statePill={
              <Pill tone="muted">
                {authorityCounts.total > 0 ? `${authorityCounts.total} matches` : "no matches"}
              </Pill>
            }
            stats={
              authorityCounts.total > 0
                ? `${authorityCounts.approved} approved of ${authorityCounts.total}`
                : "Run authority matching from AI Extraction output"
            }
          />
          <StageTile
            to={`/runs/${runId}/rdf`}
            kicker="RDF"
            title="RDF Graph"
            description="HMO ontology · Cytoscape viewer · SHACL validation"
            statePill={rdfPill(rdf)}
            stats={rdfStats(rdf)}
          />
          <StageTile
            to={`/runs/${runId}/hmo-studio`}
            kicker="HMO"
            title="HMO Wikibase Studio"
            description="IIIF manifests · Crosswalk · wikibase.cloud writer"
            statePill={<Pill tone="muted">ready</Pill>}
            stats="Generate IIIF manifests and upload to wikibase.cloud."
          />
          <StageTile
            to={`/runs/${runId}/wikidata-studio`}
            kicker="Wikidata"
            title="Wikidata Studio"
            description="Reconcile · QuickStatements · live upload"
            statePill={studioPill(studio)}
            stats={studioStats(studio)}
          />
        </div>
      </div>
    </Layout>
  );
}


// ── Tile primitive ──────────────────────────────────────────────────────


interface StageTileProps {
  to:           string;
  kicker:       string;
  title:        string;
  description:  string;
  statePill:    ReactNode;
  stats:        ReactNode;
  disabled?:    boolean;
}


function StageTile(props: StageTileProps) {
  const inner = (
    <div className={`glass p-5 space-y-3 h-full transition ${
      props.disabled ? "opacity-60" : "hover:bg-white/[0.04]"
    }`}>
      <div className="flex items-center justify-between gap-2">
        <div className="kicker">{props.kicker}</div>
        {props.statePill}
      </div>
      <h3 className="text-lg font-medium">{props.title}</h3>
      <p className="muted text-sm">{props.description}</p>
      <p className="text-sm">{props.stats}</p>
    </div>
  );
  if (props.disabled) {
    return <div className="block">{inner}</div>;
  }
  return <Link to={props.to} className="block">{inner}</Link>;
}


// ── State helpers ───────────────────────────────────────────────────────


type TileState<T> =
  | { status: "loading" }
  | { status: "ok"; data: T }
  | { status: "error"; error: string };


function errMessage(e: unknown): string {
  return e instanceof ApiError ? e.detail : String(e);
}


function extractionPill(s: TileState<ExtractionStatus>): ReactNode {
  if (s.status === "loading") return <Pill tone="muted">…</Pill>;
  if (s.status === "error")   return <Pill tone="muted">error</Pill>;
  const state = s.data.state;
  const tone =
    state === "complete" ? "ok"
    : state === "running" ? "warn"
    : state === "error" ? "err" : "muted";
  return <Pill tone={tone}>{state}</Pill>;
}


function extractionStats(s: TileState<ExtractionStatus>): string {
  if (s.status !== "ok") return "—";
  if (s.data.state !== "complete") return "Not run yet.";
  const r = s.data.records ?? 0;
  const e = s.data.entity_total ?? 0;
  return `${r} record${r === 1 ? "" : "s"} · ${e} entities`;
}


function rdfPill(s: TileState<RdfStatus>): ReactNode {
  if (s.status === "loading") return <Pill tone="muted">…</Pill>;
  if (s.status === "error")   return <Pill tone="muted">error</Pill>;
  const state = s.data.status;
  const tone =
    state === "validated" ? "ok"
    : state === "built" ? "warn"
    : state === "error" ? "err" : "muted";
  return <Pill tone={tone}>{state}</Pill>;
}


function rdfStats(s: TileState<RdfStatus>): string {
  if (s.status !== "ok") return "—";
  const t = s.data.triples_count;
  if (s.data.status === "idle" || t == null) return "Not built yet.";
  const ms = s.data.manuscripts_count ?? 0;
  return `${t.toLocaleString()} triples · ${ms} manuscript${ms === 1 ? "" : "s"}`;
}


function studioPill(s: TileState<StudioBuild>): ReactNode {
  if (s.status === "loading") return <Pill tone="muted">…</Pill>;
  if (s.status === "error")   return <Pill tone="muted">—</Pill>;
  const n = s.data.summary?.total_items ?? 0;
  return <Pill tone={n > 0 ? "ok" : "muted"}>{n} items</Pill>;
}


function studioStats(s: TileState<StudioBuild>): string {
  if (s.status !== "ok") return "—";
  const sum = s.data.summary;
  if (!sum || sum.total_items === 0) return "No items projected yet.";
  return `${sum.manuscripts} ms · ${sum.persons} persons · ${sum.works} works · ${sum.statements} statements`;
}


function Pill({
  tone, children,
}: { tone: "muted" | "ok" | "warn" | "err"; children: ReactNode }) {
  const className =
    tone === "ok"    ? "text-biu-sky"
    : tone === "warn" ? "text-yellow-300"
    : tone === "err"  ? "text-red-300"
    : "muted";
  return (
    <span className={`glass-pill px-3 py-1 text-[10px] kicker ${className}`}>
      {children}
    </span>
  );
}


function RunStatusPill({ status }: { status: string }) {
  const tone =
    status === "succeeded" ? "ok"
    : status === "running" ? "warn"
    : status === "failed"  ? "err"
    : "muted";
  return <Pill tone={tone}>{status}</Pill>;
}
