/**
 * Linked Data Explorer — run-level tab at /runs/:runId/linked-data-explorer
 *
 * Replaces the old /projects/:projectId/research route.
 *
 * Layout:
 *   ┌────────────────────────────────────────────────┐
 *   │  ← Wikidata Studio  |  ProvenanceHeader        │
 *   │  [Overview][Clusters][Network][Ownership]       │
 *   │  [Geography][SPARQL Console]                    │
 *   ├────────────────────────────────────────────────┤
 *   │  <active panel>                                 │
 *   └────────────────────────────────────────────────┘
 *
 * The project's merged RDF graph is queried via the existing research
 * endpoints — the panels receive a projectId derived from the run record.
 */
import {lazy, Suspense, useEffect, useState} from "react";
import {Link, useParams} from "react-router-dom";
import {Layout} from "@/components/Layout";
import {Runs, type RunDetail} from "@/api/runs";
import {ProvenanceHeader} from "@/components/research/ProvenanceHeader";

const SummaryPanel       = lazy(() => import("@/components/research/SummaryPanel"));
const CoOccurrencePanel  = lazy(() => import("@/components/research/CoOccurrencePanel"));
const PeopleNetworkPanel = lazy(() => import("@/components/research/PeopleNetworkPanel"));
const OwnershipPanel     = lazy(() => import("@/components/research/OwnershipChainsPanel"));
const GeographyPanel     = lazy(() => import("@/components/research/GeographyPanel"));
const SparqlConsolePanel = lazy(() => import("@/components/research/SparqlConsolePanel"));

type Tab = "summary" | "cooccurrence" | "network" | "ownership" | "geography" | "sparql";

const TABS: {id: Tab; label: string; emoji: string}[] = [
  {id: "summary",      label: "Overview",       emoji: "📊"},
  {id: "cooccurrence", label: "Clusters",        emoji: "📚"},
  {id: "network",      label: "Network",         emoji: "🕸"},
  {id: "ownership",    label: "Ownership",       emoji: "📜"},
  {id: "geography",    label: "Geography",       emoji: "🗺"},
  {id: "sparql",       label: "SPARQL Console",  emoji: "⚡"},
];

function TabBar({active, onChange}: {active: Tab; onChange: (t: Tab) => void}) {
  return (
    <nav className="flex flex-wrap gap-1 border-b border-white/10 pb-2">
      {TABS.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          className={`flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-lg transition-colors ${
            active === t.id
              ? "bg-biu-sky/15 text-biu-sky border border-biu-sky/30"
              : "text-muted hover:text-ink hover:bg-white/5 border border-transparent"
          }`}
        >
          <span>{t.emoji}</span>
          {t.label}
        </button>
      ))}
    </nav>
  );
}

function Loading() {
  return (
    <div className="flex items-center justify-center py-20 gap-3">
      <span className="animate-spin text-2xl text-biu-sky">⟳</span>
      <span className="muted text-sm">Loading panel…</span>
    </div>
  );
}

export default function LinkedDataExplorer() {
  const {runId} = useParams<{runId: string}>();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [tab, setTab] = useState<Tab>("summary");

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    Runs.get(runId).then((r) => { if (!cancelled) setRun(r); }).catch(() => {});
    return () => { cancelled = true; };
  }, [runId]);

  if (!runId) return null;

  const projectId = run?.project_id ?? "";

  return (
    <Layout>
      <div className="min-h-screen px-4 py-6 max-w-5xl mx-auto space-y-5">
        {/* Breadcrumb */}
        <div className="kicker">
          {run ? (
            <Link to={`/projects/${run.project_id}`} className="hover:text-ink underline">
              ← Project
            </Link>
          ) : (
            <span className="text-muted">← Project</span>
          )}
          {" · "}
          <Link to={`/runs/${runId}`} className="hover:text-ink underline">Run</Link>
          {" · "}
          <Link to={`/runs/${runId}/wikidata-studio`} className="hover:text-ink underline">Wikidata Studio</Link>
          {" · "}
          <span className="text-biu-sky">Linked Data Explorer</span>
        </div>

        {/* Provenance header */}
        {projectId && (
          <ProvenanceHeader projectId={projectId} runId={runId} />
        )}

        {/* Tab navigation */}
        <TabBar active={tab} onChange={setTab} />

        {/* Panel */}
        {!projectId ? (
          <Loading />
        ) : (
          <Suspense fallback={<Loading />}>
            {tab === "summary"      && <SummaryPanel       projectId={projectId} />}
            {tab === "cooccurrence" && <CoOccurrencePanel  projectId={projectId} />}
            {tab === "network"      && <PeopleNetworkPanel projectId={projectId} />}
            {tab === "ownership"    && <OwnershipPanel     projectId={projectId} />}
            {tab === "geography"    && <GeographyPanel     projectId={projectId} />}
            {tab === "sparql"       && <SparqlConsolePanel projectId={projectId} />}
          </Suspense>
        )}
      </div>
    </Layout>
  );
}
