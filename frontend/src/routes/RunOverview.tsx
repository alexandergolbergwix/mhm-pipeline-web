/**
 * Run overview — landing page for one run.
 *
 * Four workflow tiles plus a separate research-tools tile. Each tile
 * fetches its own status in parallel on mount; failures are surfaced
 * as a per-tile muted message rather than crashing the whole page.
 *
 * Tiles:
 *   1. AI Extraction      → /runs/:id/extraction
 *   2. RDF Graph          → /runs/:id/rdf
 *   3. HMO catalogue review         → /runs/:id/hmo-studio
 *   4. Wikidata Studio              → /runs/:id/wikidata-studio
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { ApiError } from "@/api/client";
import { Extraction, type ExtractionStatus } from "@/api/extraction";
import { RunJobs } from "@/api/runJobs";
import { Rdf, type RdfStatus } from "@/api/rdf";
import { Runs, type RunDetail } from "@/api/runs";
import { Studio, type StudioBuild } from "@/api/wikidataStudio";
import { HmoStudio, type HmoStudioStatus } from "@/api/hmoStudio";
import {Glass, GlassPill} from "@/components/glass";
import {idsFingerprint} from "@/utils/renderStable";


export default function RunOverview() {
  const { runId } = useParams<{ runId: string }>();

  const [run, setRun]           = useState<RunDetail | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const [extraction, setExtraction]       = useState<TileState<ExtractionStatus>>({ status: "loading" });
  const [rdf, setRdf]                     = useState<TileState<RdfStatus>>({ status: "loading" });
  const [hmo, setHmo]                     = useState<TileState<HmoStudioStatus>>({ status: "loading" });
  const [studio, setStudio]               = useState<TileState<StudioBuild>>({ status: "loading" });
  const [activeJobs, setActiveJobs]       = useState<string[]>([]);
  const activeJobsRef = useRef("");

  useEffect(() => {
    let cancelled = false;
    function applyKinds(jobs: Awaited<ReturnType<typeof RunJobs.listMine>>["jobs"]) {
      const kinds = jobs.filter((j) => j.run_id === runId).map((j) => j.kind);
      const fp = idsFingerprint(kinds);
      if (fp === activeJobsRef.current) return;
      activeJobsRef.current = fp;
      setActiveJobs(kinds);
    }
    void RunJobs.listMine(true).then(({jobs}) => {
      if (!cancelled) applyKinds(jobs);
    });
    const id = window.setInterval(() => {
      void RunJobs.listMine(true).then(({jobs}) => {
        if (!cancelled) applyKinds(jobs);
      });
    }, 4000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [runId]);

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

      // Fire all stage-status calls in parallel. We deliberately
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
      HmoStudio.status(runId).then(
        (s) => !cancelled && setHmo({ status: "ok", data: s }),
        (e) => !cancelled && setHmo({ status: "error", error: errMessage(e) }),
      );
      Studio.build(runId, {approvedOnly: false}).then(
        (s) => !cancelled && setStudio({ status: "ok", data: s }),
        (e) => !cancelled && setStudio({ status: "error", error: errMessage(e) }),
      );
    }
    void fetchAll();
    return () => { cancelled = true; };
  }, [runId]);



  if (runError) {
    return (
      <Layout>
        <Glass className="p-6 text-danger">{runError}</Glass>
      </Layout>
    );
  }
  if (!run) {
    return <Layout><p className="muted">Loading run…</p></Layout>;
  }

  return (
    <Layout>
      <div className="space-y-6">
        <Glass as="section" className="p-6 space-y-2">
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
          {run.error && <p className="text-danger text-sm">{run.error}</p>}
        </Glass>

        <NextStep runId={runId} extraction={extraction} rdf={rdf} hmo={hmo} />

        <section aria-labelledby="workflow-heading" className="space-y-3">
          <div>
            <div className="kicker">Your review workflow</div>
            <h2 id="workflow-heading" className="text-lg font-medium">From catalogue records to approved data</h2>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <StageTile
            to={`/runs/${runId}/extraction`}
            kicker="AI"
            title="Extracted catalogue information"
            description="Names, places, contents, and genres identified from the records."
            statePill={extractionPill(extraction)}
            stats={
              <>
                {extractionStats(extraction)}
                {activeJobs.includes("extraction") && (
                  <span className="text-warn ml-2">· job running</span>
                )}
              </>
            }
          />
          <StageTile
            to={`/runs/${runId}/rdf`}
            kicker="RDF"
            title="Structured manuscript data"
            description="A connected view of manuscripts, people, places, and works."
            statePill={rdfPill(rdf)}
            stats={
              <>
                {rdfStats(rdf)}
                {activeJobs.includes("rdf_build") && (
                  <span className="text-warn ml-2">· build running</span>
                )}
              </>
            }
          />
          <StageTile
            to={`/runs/${runId}/hmo-studio`}
            kicker="HMO"
            title="HMO catalogue review"
            description="Review generated catalogue entries before they are published."
            statePill={hmoPill(hmo)}
            stats={hmoStats(hmo)}
          />
          <StageTile
            to={`/runs/${runId}/wikidata-studio`}
            kicker="Wikidata"
            title="External authority review"
            description="Check links to people, works, and institutions in external catalogues."
            statePill={studioPill(studio)}
            stats={
              <>
                {studioStats(studio)}
                {(activeJobs.includes("wikidata_studio_build")
                  || activeJobs.includes("wikidata_upload")) && (
                  <span className="text-warn ml-2">· job running</span>
                )}
              </>
            }
          />
          </div>
        </section>

        <section aria-labelledby="research-tools-heading" className="space-y-3">
          <div>
            <div className="kicker">Research tools</div>
            <h2 id="research-tools-heading" className="text-lg font-medium">Explore connected records</h2>
          </div>
          <StageTile
            to={`/runs/${runId}/linked-data-explorer`}
            kicker="LOD"
            title="Linked Data Explorer"
            description="Explore relationships across the manuscript catalogue and linked sources."
            statePill={<Pill tone="muted">explore</Pill>}
            stats="For research and discovery; it does not change the catalogue."
          />
        </section>
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
    <Glass className={`p-5 space-y-3 h-full transition ${
      props.disabled ? "opacity-60" : "hover:bg-white/[0.04]"
    }`}>
      <div className="flex items-center justify-between gap-2">
        <div className="kicker">{props.kicker}</div>
        {props.statePill}
      </div>
      <h3 className="text-lg font-medium">{props.title}</h3>
      <p className="muted text-sm">{props.description}</p>
      <p className="text-sm">{props.stats}</p>
    </Glass>
  );
  if (props.disabled) {
    return <div className="block">{inner}</div>;
  }
  return <Link to={props.to} className="block">{inner}</Link>;
}


interface NextStepValue {
  path: "extraction" | "rdf" | "hmo-studio";
  title: string;
  description: string;
  action: string;
}


function NextStep({
  runId, extraction, rdf, hmo,
}: {
  runId: string | undefined;
  extraction: TileState<ExtractionStatus>;
  rdf: TileState<RdfStatus>;
  hmo: TileState<HmoStudioStatus>;
}) {
  const step = nextStep(extraction, rdf, hmo);
  return (
    <Glass as="section" className="p-5 space-y-2 border border-biu-sky/30" aria-labelledby="next-step-heading">
      <div className="kicker">Recommended next step</div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="next-step-heading" className="text-lg font-medium">{step.title}</h2>
          <p className="muted text-sm mt-1">{step.description}</p>
        </div>
        {runId && <Link to={`/runs/${runId}/${step.path}`} className="button-primary">{step.action}</Link>}
      </div>
    </Glass>
  );
}


function nextStep(
  extraction: TileState<ExtractionStatus>,
  rdf: TileState<RdfStatus>,
  hmo: TileState<HmoStudioStatus>,
): NextStepValue {
  if (extraction.status === "error" || (extraction.status === "ok" && extraction.data.state === "error")) {
    return {
      path: "extraction",
      title: "Check the catalogue information",
      description: "The extraction step needs attention before the rest of the workflow can continue.",
      action: "Open extraction review",
    };
  }
  if (extraction.status !== "ok" || extraction.data.state !== "complete") {
    return {
      path: "extraction",
      title: "Review extracted catalogue information",
      description: "Check the names, places, contents, and genres identified from the records.",
      action: "Open extraction review",
    };
  }
  if (rdf.status === "error" || (rdf.status === "ok" && rdf.data.status === "error")) {
    return {
      path: "rdf",
      title: "Check the structured manuscript data",
      description: "The connected manuscript data needs attention before catalogue entries can be prepared.",
      action: "Open structured data",
    };
  }
  if (rdf.status !== "ok" || rdf.data.status === "idle") {
    return {
      path: "rdf",
      title: "Build the structured manuscript data",
      description: "Prepare the connected manuscript, person, place, and work information.",
      action: "Open structured data",
    };
  }
  if (hmo.status !== "ok" || !hmo.data.canonical_ready) {
    return {
      path: "hmo-studio",
      title: "Review HMO catalogue entries",
      description: "Check the generated entries and preview what will be published to the HMO catalogue.",
      action: "Open HMO review",
    };
  }
  return {
    path: "hmo-studio",
    title: "Review confirmed HMO catalogue entries",
    description: "All built entries have a confirmed live copy. Review them before your next editorial step.",
    action: "Open HMO review",
  };
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
  if (s.status === "error")   return <Pill tone="err">needs attention</Pill>;
  const state = s.data.state;
  const tone =
    state === "complete" ? "ok"
    : state === "running" ? "warn"
    : state === "error" ? "err" : "muted";
  const label = state === "complete" ? "ready for review" : state === "running" ? "in progress" : state === "error" ? "needs attention" : "not started";
  return <Pill tone={tone}>{label}</Pill>;
}


function extractionStats(s: TileState<ExtractionStatus>): string {
  if (s.status === "loading") return "Checking catalogue information…";
  if (s.status === "error") return "Could not load status. Open this review to retry.";
  if (s.data.state !== "complete") return "Not run yet.";
  const r = s.data.records ?? 0;
  const e = s.data.entity_total ?? 0;
  return `${r} record${r === 1 ? "" : "s"} · ${e} entities`;
}


function rdfPill(s: TileState<RdfStatus>): ReactNode {
  if (s.status === "loading") return <Pill tone="muted">…</Pill>;
  if (s.status === "error")   return <Pill tone="err">status unavailable</Pill>;
  const state = s.data.status;
  const tone =
    state === "validated" ? "ok"
    : state === "built" ? "warn"
    : state === "error" ? "err" : "muted";
  const label = state === "validated" ? "ready" : state === "built" ? "built" : state === "error" ? "needs attention" : "not started";
  return <Pill tone={tone}>{label}</Pill>;
}


function rdfStats(s: TileState<RdfStatus>): string {
  if (s.status === "loading") return "Checking structured data…";
  if (s.status === "error") return "Could not load status. Open this review to retry.";
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
  return `${sum.manuscripts} manuscripts · ${sum.persons} people · ${sum.works} works · ${sum.statements.toLocaleString()} relationships`;
}


function hmoPill(s: TileState<HmoStudioStatus>): ReactNode {
  if (s.status === "loading") return <Pill tone="muted">checking</Pill>;
  if (s.status === "error") return <Pill tone="err">status unavailable</Pill>;
  if (s.data.canonical_ready) return <Pill tone="ok">confirmed</Pill>;
  if (s.data.canonical_live_count > 0) return <Pill tone="warn">partly confirmed</Pill>;
  if (s.data.state === "uploaded") return <Pill tone="warn">published, awaiting confirmation</Pill>;
  if (s.data.state === "built") return <Pill tone="warn">ready for review</Pill>;
  if (s.data.rdf_present) return <Pill tone="warn">ready to prepare</Pill>;
  return <Pill tone="muted">not prepared</Pill>;
}


function hmoStats(s: TileState<HmoStudioStatus>): string {
  if (s.status === "loading") return "Checking catalogue preparation…";
  if (s.status === "error") return "Could not load catalogue status. Open the HMO review page to retry.";
  if (s.data.canonical_ready) return `${s.data.canonical_live_count} catalogue entries confirmed live.`;
  if (s.data.canonical_live_count > 0) {
    return `${s.data.canonical_live_count} catalogue entries confirmed; review the remaining entries.`;
  }
  if (s.data.state === "uploaded") return "Published entries are awaiting live confirmation.";
  if (s.data.state === "built") return `${s.data.manifest_count} IIIF manifest${s.data.manifest_count === 1 ? "" : "s"} ready for review.`;
  if (s.data.rdf_present) return "Prepare catalogue entries for editorial review.";
  return "Prepare catalogue entries after the structured data is built.";
}


function Pill({
  tone, children,
}: { tone: "muted" | "ok" | "warn" | "err"; children: ReactNode }) {
  const className =
    tone === "ok"    ? "text-biu-sky"
    : tone === "warn" ? "text-warn"
    : tone === "err"  ? "text-danger"
    : "muted";
  return (
    <GlassPill className={`px-3 py-1 text-[10px] kicker ${className}`}>
      {children}
    </GlassPill>
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
