/**
 * Research Explorer — top-level page.
 *
 * Accessible at /projects/:projectId/research
 *
 * Panelled layout:
 *   ┌─────────────────────────────────────────────┐
 *   │  ← Project  Research Explorer               │
 *   │  [Overview] [Clusters] [Network] [Ownership]│
 *   │  [Geography]                                │
 *   ├─────────────────────────────────────────────┤
 *   │  <active panel>                             │
 *   └─────────────────────────────────────────────┘
 */
import {lazy, Suspense, useState} from "react";
import {Link, useParams} from "react-router-dom";

const SummaryPanel       = lazy(() => import("@/components/research/SummaryPanel"));
const CoOccurrencePanel  = lazy(() => import("@/components/research/CoOccurrencePanel"));
const PeopleNetworkPanel = lazy(() => import("@/components/research/PeopleNetworkPanel"));
const OwnershipPanel     = lazy(() => import("@/components/research/OwnershipChainsPanel"));
const GeographyPanel     = lazy(() => import("@/components/research/GeographyPanel"));

type Tab = "summary" | "cooccurrence" | "network" | "ownership" | "geography";

const TABS: {id: Tab; label: string; emoji: string}[] = [
  {id: "summary",      label: "Overview",   emoji: "📊"},
  {id: "cooccurrence", label: "Clusters",   emoji: "📚"},
  {id: "network",      label: "Network",    emoji: "🕸"},
  {id: "ownership",    label: "Ownership",  emoji: "📜"},
  {id: "geography",    label: "Geography",  emoji: "🗺"},
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
      <span className="animate-spin text-2xl">⟳</span>
      <span className="muted text-sm">Loading panel…</span>
    </div>
  );
}

export default function Research() {
  const {projectId} = useParams<{projectId: string}>();
  const [tab, setTab] = useState<Tab>("summary");

  if (!projectId) return null;

  return (
    <div className="min-h-screen px-4 py-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="space-y-1">
        <div className="kicker">
          <Link to={`/projects/${projectId}`} className="hover:text-ink underline">
            ← Project
          </Link>
        </div>
        <h1 className="text-2xl font-bold text-ink">Research Explorer</h1>
        <p className="muted text-sm max-w-2xl">
          Explore the linked-open-data output of the pipeline: textual clusters, social
          networks, ownership chains, and geographic distribution — queries your curators
          cannot ask of a flat MARC catalogue.
        </p>
      </div>

      {/* Tab navigation */}
      <TabBar active={tab} onChange={setTab} />

      {/* Panel */}
      <Suspense fallback={<Loading />}>
        {tab === "summary"      && <SummaryPanel       projectId={projectId} />}
        {tab === "cooccurrence" && <CoOccurrencePanel  projectId={projectId} />}
        {tab === "network"      && <PeopleNetworkPanel projectId={projectId} />}
        {tab === "ownership"    && <OwnershipPanel     projectId={projectId} />}
        {tab === "geography"    && <GeographyPanel     projectId={projectId} />}
      </Suspense>
    </div>
  );
}
