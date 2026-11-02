import {lazy, Suspense, useEffect, useMemo, useState} from "react";
import {Glass} from "@/components/glass";
import {ResearchAgent, type ResearchArtifact} from "@/api/researchAgent";
import type {WikidataPlacePoint} from "@/components/research/WikidataPlacesPanel";
import {
  CHART_PALETTE,
  SignalAreaChart,
  SignalBarList,
  SignalColumnChart,
  SignalDataTable,
  SignalDonutChart,
  SignalFunnel,
  SignalStatGrid,
  type SignalBarItem,
  type SignalSeries,
  type SignalStatItem,
} from "./ResearchCharts";
import {ResearchMarkdown} from "./ResearchMarkdown";

const ProvenanceMapPanel = lazy(() => import("@/components/research/ProvenanceMapPanel"));
const WikidataPlacesPanel = lazy(() => import("@/components/research/WikidataPlacesPanel"));
const PeopleNetworkPanel = lazy(() => import("@/components/research/PeopleNetworkPanel"));
const CoOccurrencePanel = lazy(() => import("@/components/research/CoOccurrencePanel"));
const GeographyPanel = lazy(() => import("@/components/research/GeographyPanel"));
const OwnershipChainsPanel = lazy(() => import("@/components/research/OwnershipChainsPanel"));
const EdaDashboardPanel = lazy(() => import("@/components/research/EdaDashboardPanel"));

const LIVE_EDA_KEY = "__live_eda__";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item ?? ""));
}

function toTableRows(value: unknown): Array<Array<string | number | null>> {
  if (!Array.isArray(value)) return [];
  return value.map((row) => {
    if (!Array.isArray(row)) return [];
    return row.map((cell) => {
      if (cell === null || cell === undefined) return null;
      if (typeof cell === "number" || typeof cell === "string") return cell;
      return String(cell);
    });
  });
}

function toPlacePoints(value: unknown): WikidataPlacePoint[] {
  if (!Array.isArray(value)) return [];
  const out: WikidataPlacePoint[] = [];
  for (const item of value) {
    if (!isRecord(item)) continue;
    if (typeof item.lat !== "number" || typeof item.lon !== "number") continue;
    const rawMs = Array.isArray(item.manuscripts) ? item.manuscripts : [];
    const manuscripts = rawMs.flatMap((m) => {
      if (!isRecord(m)) return [];
      return [
        {
          item_qid: String(m.item_qid ?? ""),
          item_label: String(m.item_label ?? ""),
          property: String(m.property ?? ""),
          property_label: typeof m.property_label === "string" ? m.property_label : undefined,
        },
      ];
    });
    out.push({
      place_qid: String(item.place_qid ?? ""),
      place_label: String(item.place_label ?? ""),
      lat: item.lat,
      lon: item.lon,
      manuscripts,
    });
  }
  return out;
}

function artifactBadge(art: ResearchArtifact): string {
  const key = art.artifact_key.toLowerCase();
  if (art.kind === "map") return "Map";
  if (art.kind === "chart" || art.kind === "view") return "Chart";
  if (key.includes("network")) return "Network";
  if (key.includes("cooccurrence") || key.includes("cluster")) return "Clusters";
  if (key.includes("geography")) return "Geography";
  if (key.includes("ownership")) return "Ownership";
  if (art.kind === "sparql" || art.kind === "table") return "Dataset";
  return "Report";
}

export function ResearchCanvas({
  projectId,
  threadId,
  artifacts,
  activeKey,
  onSelect,
  onSaved,
}: {
  projectId: string;
  threadId: string;
  artifacts: ResearchArtifact[];
  activeKey: string | null;
  onSelect: (key: string) => void;
  onSaved: (artifact: ResearchArtifact) => void;
}) {
  const isLiveEdaSelected = activeKey === LIVE_EDA_KEY;
  const active = isLiveEdaSelected
    ? null
    : (artifacts.find((a) => a.artifact_key === activeKey) ?? artifacts.at(-1) ?? null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!active && !isLiveEdaSelected) setExpanded(false);
  }, [active?.artifact_key, isLiveEdaSelected]);

  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  const body = active ? (
    <ArtifactBody
      projectId={projectId}
      threadId={threadId}
      artifact={active}
      onSaved={onSaved}
    />
  ) : (
    <Suspense fallback={<p className="muted text-sm">Loading corpus dashboard…</p>}>
      <EdaDashboardPanel projectId={projectId} />
    </Suspense>
  );

  return (
    <Glass className="flex flex-col h-full min-h-[42rem] gap-3">
      <div className="space-y-2 border-b border-white/10 pb-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          {artifacts.length === 0 ? (
            <div className="flex items-center gap-2">
              <span className="inline-block h-2 w-2 rounded-full bg-biu-sky" />
              <span className="text-xs font-medium text-ink">Live Corpus Signal View</span>
              <span className="text-xs muted">· Canvas is empty. Ask in chat or use an action.</span>
            </div>
          ) : (
            <label className="flex items-center gap-2 min-w-0 flex-1">
              <span className="text-xs muted shrink-0 font-medium">Canvas ({artifacts.length}):</span>
              <select
                value={isLiveEdaSelected ? LIVE_EDA_KEY : (active?.artifact_key ?? "")}
                onChange={(e) => onSelect(e.target.value)}
                aria-label="Canvas artifact history — newest last"
                data-testid="canvas-artifact-select"
                className="flex-1 min-w-0 max-w-[32rem] truncate text-xs px-2.5 py-1.5 rounded-lg border border-white/10 bg-[var(--surface-inset)] text-ink"
              >
                {artifacts.map((art, i) => (
                  <option key={art.artifact_key} value={art.artifact_key}>
                    {artifacts.length - i}. {art.title || art.artifact_key}
                  </option>
                ))}
                <option value={LIVE_EDA_KEY}>★ Live Corpus EDA Dashboard</option>
              </select>
            </label>
          )}
          <div className="flex items-center gap-2 text-xs">
            {active ? (
              <>
                <a
                  className="rounded-lg border border-white/10 bg-white/[0.03] hover:bg-white/10 px-2.5 py-1 text-biu-sky transition-colors"
                  href={ResearchAgent.downloadUrl(threadId, active.artifact_key, downloadFormat(active.kind))}
                >
                  Download
                </a>
                <a
                  className="rounded-lg border border-white/10 bg-white/[0.03] hover:bg-white/10 px-2.5 py-1 text-biu-sky transition-colors"
                  href={ResearchAgent.exportUrl(threadId)}
                >
                  Export session
                </a>
              </>
            ) : null}
            <button
              type="button"
              className="rounded-lg border border-biu-sky/30 bg-biu-sky/10 hover:bg-biu-sky/20 px-2.5 py-1 text-biu-sky transition-colors"
              onClick={() => setExpanded(true)}
              title="Expand to full screen"
              aria-label="Expand canvas"
            >
              ⛶ Expand
            </button>
          </div>
        </div>

        {artifacts.length > 0 ? (
          <div
            className="flex items-center gap-1.5 overflow-x-auto pb-0.5 [scrollbar-width:thin]"
            role="tablist"
            aria-label="Canvas artifacts"
          >
            <button
              type="button"
              role="tab"
              aria-selected={isLiveEdaSelected}
              onClick={() => onSelect(LIVE_EDA_KEY)}
              className={`shrink-0 inline-flex items-center gap-1.5 rounded-xl border px-2.5 py-1 text-xs transition-colors ${
                isLiveEdaSelected
                  ? "border-biu-sky bg-biu-sky/20 text-biu-sky font-medium"
                  : "border-white/10 bg-white/[0.03] text-muted hover:text-ink hover:border-white/20"
              }`}
            >
              <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.2 rounded bg-biu-sky/15 text-biu-sky">
                EDA
              </span>
              <span>Corpus Signals</span>
            </button>
            {[...artifacts].reverse().map((art) => {
              const selected = !isLiveEdaSelected && active?.artifact_key === art.artifact_key;
              return (
                <button
                  key={art.artifact_key}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  onClick={() => onSelect(art.artifact_key)}
                  className={`shrink-0 inline-flex items-center gap-1.5 rounded-xl border px-2.5 py-1 text-xs transition-colors max-w-[16rem] ${
                    selected
                      ? "border-biu-sky bg-biu-sky/20 text-ink font-medium"
                      : "border-white/10 bg-white/[0.03] text-muted hover:text-ink hover:border-white/20"
                  }`}
                >
                  <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.2 rounded bg-white/10 text-biu-sky shrink-0">
                    {artifactBadge(art)}
                  </span>
                  <span className="truncate" dir="auto">{art.title || art.artifact_key}</span>
                </button>
              );
            })}
          </div>
        ) : null}
      </div>

      <div className="flex-1 overflow-auto pr-1" data-testid="research-canvas">
        {body}
      </div>

      {expanded ? (
        <div
          className="fixed inset-0 z-50 flex flex-col gap-3 p-4 sm:p-6 overflow-hidden"
          style={{background: "var(--body-bg)"}}
          role="dialog"
          aria-modal="true"
        >
          <div className="flex items-center justify-between gap-2 border-b border-white/10 pb-3">
            <span className="text-base font-semibold text-ink truncate">
              {active ? (active.title || active.artifact_key) : "Live Corpus EDA & Signals"}
            </span>
            <div className="flex gap-3 text-xs">
              {active ? (
                <a
                  className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-biu-sky hover:underline"
                  href={ResearchAgent.downloadUrl(threadId, active.artifact_key, downloadFormat(active.kind))}
                >
                  Download
                </a>
              ) : null}
              <button
                type="button"
                className="rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-ink hover:bg-white/10"
                onClick={() => setExpanded(false)}
                aria-label="Close full screen"
              >
                ✕ Close
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-auto" data-testid="research-canvas-expanded">
            {active ? (
              <ArtifactBody
                projectId={projectId}
                threadId={threadId}
                artifact={active}
                onSaved={onSaved}
              />
            ) : (
              <Suspense fallback={<p className="muted text-sm">Loading corpus dashboard…</p>}>
                <EdaDashboardPanel projectId={projectId} />
              </Suspense>
            )}
          </div>
        </div>
      ) : null}
    </Glass>
  );
}

function downloadFormat(kind: string): string {
  if (kind === "markdown") return "md";
  if (kind === "sparql" || kind === "table") return "csv";
  return "json";
}

function ArtifactBody({
  projectId,
  threadId,
  artifact,
  onSaved,
}: {
  projectId: string;
  threadId: string;
  artifact: ResearchArtifact;
  onSaved: (artifact: ResearchArtifact) => void;
}) {
  const key = artifact.artifact_key.toLowerCase();

  if (artifact.kind === "map" && Array.isArray(artifact.content?.points)) {
    const points = toPlacePoints(artifact.content.points);
    return (
      <Suspense fallback={<p className="muted text-sm">Loading map…</p>}>
        <WikidataPlacesPanel points={points} />
      </Suspense>
    );
  }
  if (artifact.kind === "map") {
    const cn = typeof artifact.content?.cn === "string" && artifact.content.cn.trim()
      ? artifact.content.cn.trim()
      : undefined;
    const isCorpusArtifact =
      !cn &&
      (artifact.content?.cn === null ||
        typeof artifact.content?.manuscript_count === "number" ||
        artifact.title.toLowerCase().includes("corpus"));
    return (
      <Suspense fallback={<p className="muted text-sm">Loading map…</p>}>
        <ProvenanceMapPanel
          projectId={projectId}
          initialCn={cn}
          initialMode={isCorpusArtifact ? "corpus" : "single"}
        />
      </Suspense>
    );
  }
  if (artifact.kind === "sparql") {
    return <SparqlArtifactView artifact={artifact} />;
  }
  if (artifact.kind === "chart" || artifact.kind === "view") {
    return <ChartView content={artifact.content} title={artifact.title} />;
  }
  if (artifact.kind === "json" && key.includes("network")) {
    return (
      <Suspense fallback={<p className="muted text-sm">Loading network…</p>}>
        <PeopleNetworkPanel projectId={projectId} />
      </Suspense>
    );
  }
  if (artifact.kind === "json" && (key.includes("cooccurrence") || key.includes("cluster"))) {
    return (
      <Suspense fallback={<p className="muted text-sm">Loading clusters…</p>}>
        <CoOccurrencePanel projectId={projectId} />
      </Suspense>
    );
  }
  if (artifact.kind === "json" && key.includes("geography")) {
    return (
      <Suspense fallback={<p className="muted text-sm">Loading geography…</p>}>
        <GeographyPanel projectId={projectId} />
      </Suspense>
    );
  }
  if (artifact.kind === "json" && key.includes("ownership")) {
    return (
      <Suspense fallback={<p className="muted text-sm">Loading ownership chains…</p>}>
        <OwnershipChainsPanel projectId={projectId} />
      </Suspense>
    );
  }
  if (artifact.kind === "json" && (key.includes("summary") || key.includes("eda"))) {
    return (
      <Suspense fallback={<p className="muted text-sm">Loading corpus dashboard…</p>}>
        <EdaDashboardPanel projectId={projectId} />
      </Suspense>
    );
  }
  if (artifact.kind === "table" || hasTabular(artifact.content)) {
    return <TableView content={artifact.content} title={artifact.title} />;
  }
  return (
    <MarkdownEditor
      projectId={projectId}
      threadId={threadId}
      artifact={artifact}
      onSaved={onSaved}
    />
  );
}

function hasTabular(content: Record<string, unknown>): boolean {
  return Array.isArray(content.columns) && Array.isArray(content.rows);
}

function SparqlArtifactView({artifact}: {artifact: ResearchArtifact}) {
  const [showQuery, setShowQuery] = useState(false);
  const rawSource = String(artifact.content?.source ?? "hmo").toLowerCase();
  const query = typeof artifact.content?.query === "string" ? artifact.content.query : "";

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-white/10 bg-[var(--surface-inset)] px-3 py-2">
        <div className="flex items-center gap-1.5">
          <span className="text-[11px] text-muted mr-1">Endpoint:</span>
          {(
            [
              {id: "hmo", label: "HMO Graph"},
              {id: "wikibase", label: "Wikibase"},
              {id: "wikidata", label: "Wikidata"},
            ] as const
          ).map((ep) => {
            const active = rawSource === ep.id || (ep.id === "hmo" && !["wikibase", "wikidata"].includes(rawSource));
            return (
              <button
                key={ep.id}
                type="button"
                className={`rounded-lg px-2.5 py-1 text-xs transition-colors ${
                  active
                    ? "bg-biu-sky/20 border border-biu-sky/40 text-biu-sky font-medium"
                    : "border border-white/10 text-muted"
                }`}
              >
                {ep.label}
              </button>
            );
          })}
        </div>
        {query ? (
          <button
            type="button"
            onClick={() => setShowQuery((v) => !v)}
            className="text-xs text-biu-sky hover:underline"
          >
            {showQuery ? "Hide SPARQL query" : "Show SPARQL query"}
          </button>
        ) : null}
      </div>

      {showQuery && query ? (
        <pre className="text-xs rounded-xl bg-[var(--code-bg)] border border-white/10 px-3 py-2.5 overflow-auto font-mono text-ink">
          {query}
        </pre>
      ) : null}

      {hasTabular(artifact.content) ? (
        <TableView content={artifact.content} title={artifact.title} />
      ) : (
        <p className="muted text-sm">
          {String(artifact.content?.text ?? "Query result stored. Ask the assistant for a table or chart.")}
        </p>
      )}
    </div>
  );
}

interface ChartGroupItem {
  label: string;
  count: number;
  internal?: number;
}

interface ChartGroup {
  name: string;
  items: ChartGroupItem[];
}

function parseChartGroups(value: unknown): ChartGroup[] {
  if (!Array.isArray(value)) return [];
  const out: ChartGroup[] = [];
  for (const g of value) {
    if (!isRecord(g)) continue;
    const rawItems = Array.isArray(g.items) ? g.items : [];
    const items: ChartGroupItem[] = [];
    for (const item of rawItems) {
      if (!isRecord(item)) continue;
      items.push({
        label: String(item.label ?? ""),
        count: Number(item.count) || 0,
        internal: typeof item.internal === "number" ? item.internal : undefined,
      });
    }
    out.push({name: String(g.name ?? "Other"), items});
  }
  return out;
}

function parseSignalSeries(value: unknown): SignalSeries[] {
  if (!Array.isArray(value)) return [];
  const out: SignalSeries[] = [];
  for (let i = 0; i < value.length; i += 1) {
    const s = value[i];
    if (!isRecord(s) || !Array.isArray(s.values)) continue;
    out.push({
      name: String(s.name ?? `Series ${i + 1}`),
      values: s.values.map((v) => (typeof v === "number" && Number.isFinite(v) ? v : null)),
      color: typeof s.color === "string" ? s.color : CHART_PALETTE[i % CHART_PALETTE.length],
    });
  }
  return out;
}

const FAMILY_COLORS: Record<string, string> = {
  "Wikidata properties": "#77cce5",
  "MHM ontology": "#a78bfa",
  "FRBRoo / LRMoo (IFLA)": "#f59e0b",
  "CIDOC CRM": "#34d399",
  "RDF / RDFS": "#60a5fa",
  "Other": "#9aa3b2",
};

function ChartView({content, title}: {content: Record<string, unknown>; title?: string}) {
  const groups = useMemo(() => parseChartGroups(content.groups), [content.groups]);
  const labels = useMemo(() => toStringArray(content.labels), [content.labels]);
  const series = useMemo(() => parseSignalSeries(content.series), [content.series]);
  const kind = String(content.kind ?? content.chart ?? "bar").toLowerCase();
  const unit = typeof content.unit === "string" ? content.unit : undefined;
  const source = typeof content.source === "string" ? content.source : undefined;

  if (labels.length > 0 && series.length > 0) {
    const firstSeries = series[0];
    const barItems: SignalBarItem[] = labels.map((label, idx) => ({
      key: `${idx}-${label}`,
      label,
      value: firstSeries.values[idx] ?? 0,
      color: CHART_PALETTE[idx % CHART_PALETTE.length],
    }));

    return (
      <div className="space-y-4 rounded-2xl border border-white/10 bg-[var(--surface-inset)] p-4">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 pb-2">
          <div>
            <div className="kicker">Visual Signal</div>
            <h3 className="text-base font-semibold text-ink" dir="auto">
              {String(content.title ?? title ?? "Chart")}
            </h3>
          </div>
          {unit ? (
            <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-0.5 text-xs text-muted">
              Unit: {unit}
            </span>
          ) : null}
        </div>

        {kind === "stats" ? (
          <SignalStatGrid
            items={barItems.map((b, i) => ({
              id: b.key,
              label: b.label,
              value: b.value,
              unit,
              color: CHART_PALETTE[i % CHART_PALETTE.length],
            }))}
            cols={barItems.length >= 4 ? 4 : 3}
          />
        ) : kind === "donut" ? (
          <SignalDonutChart items={barItems} unit={unit} centerLabel={firstSeries.name} />
        ) : kind === "line" || kind === "area" ? (
          <SignalAreaChart labels={labels} series={series} unit={unit} />
        ) : kind === "barlist" ? (
          <SignalBarList items={barItems} unit={unit} />
        ) : kind === "funnel" ? (
          <SignalFunnel steps={barItems.map((b) => ({label: b.label, value: b.value}))} unit={unit} />
        ) : (
          <SignalColumnChart
            labels={labels}
            series={series}
            unit={unit}
            stacked={kind === "stacked"}
          />
        )}

        {source ? (
          <div className="text-xs text-muted border-t border-white/5 pt-2">
            Source: {source}
          </div>
        ) : null}
      </div>
    );
  }

  const total =
    Number(content.total_claims) ||
    groups.reduce((s, g) => s + g.items.reduce((a, i) => a + (i.count || 0), 0), 0);
  const internal = Number(content.internal_links) || 0;
  const distinctProps =
    Number(content.distinct_properties) || groups.reduce((s, g) => s + g.items.length, 0);

  const statItems: SignalStatItem[] = [
    {id: "claims", label: "Total Claims", value: total, color: "#77cce5", accent: true},
    {id: "properties", label: "Distinct Properties", value: distinctProps, color: "#a78bfa"},
    {id: "items", label: "Uploaded Items", value: String(content.items_counted ?? "—"), color: "#34d399"},
    ...(internal > 0
      ? [{id: "internal", label: "Internal Project Links", value: internal, color: "#f59e0b", accent: true}]
      : []),
  ];

  const familyDonutItems: SignalBarItem[] = groups.map((g, idx) => ({
    key: g.name,
    label: g.name,
    value: g.items.reduce((a, i) => a + (i.count || 0), 0),
    color: FAMILY_COLORS[g.name] ?? CHART_PALETTE[idx % CHART_PALETTE.length],
  }));

  return (
    <div className="space-y-4">
      <SignalStatGrid items={statItems} cols={statItems.length >= 4 ? 4 : 3} />

      {familyDonutItems.length > 1 ? (
        <div className="rounded-2xl border border-white/10 bg-[var(--surface-inset)] p-4 space-y-2">
          <div className="text-xs font-semibold uppercase tracking-wider text-muted">
            Share by Ontology / Property Family
          </div>
          <SignalDonutChart items={familyDonutItems} unit="claims" centerLabel="claims" />
        </div>
      ) : null}

      {groups.map((group, gIdx) => {
        const color = FAMILY_COLORS[group.name] ?? CHART_PALETTE[gIdx % CHART_PALETTE.length];
        const groupTotal = group.items.reduce((a, i) => a + (i.count || 0), 0);
        const barItems: SignalBarItem[] = group.items.map((item) => ({
          key: item.label,
          label: item.label,
          value: item.count,
          secondaryValue: item.internal,
          secondaryLabel: "Claims whose target is one of our own uploaded QIDs",
          color,
        }));
        return (
          <div
            key={group.name}
            className="rounded-2xl border border-white/10 bg-[var(--surface-inset)] p-4 space-y-3"
          >
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="inline-block h-2.5 w-2.5 rounded-full" style={{background: color}} />
                <span className="text-sm font-semibold text-ink">{group.name}</span>
              </div>
              <span className="text-xs muted tabular-nums font-medium">
                {groupTotal.toLocaleString()} claims · {total > 0 ? Math.round((groupTotal / total) * 100) : 0}%
              </span>
            </div>
            <SignalBarList items={barItems} total={groupTotal} color={color} />
          </div>
        );
      })}
      {internal > 0 ? (
        <p className="text-xs muted">
          ↺ marks properties whose claim target is one of our own uploaded QIDs — links between items in this project.
        </p>
      ) : null}
    </div>
  );
}

function TableView({content, title}: {content: Record<string, unknown>; title?: string}) {
  const columns = toStringArray(content.columns);
  const rows = toTableRows(content.rows);
  const source = typeof content.source === "string" ? content.source : undefined;
  return <SignalDataTable columns={columns} rows={rows} title={title} source={source} />;
}

function MarkdownEditor({
  projectId,
  threadId,
  artifact,
  onSaved,
}: {
  projectId: string;
  threadId: string;
  artifact: ResearchArtifact;
  onSaved: (artifact: ResearchArtifact) => void;
}) {
  const initial = String(artifact.content.text ?? JSON.stringify(artifact.content, null, 2));
  const [text, setText] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState(false);

  const isOverviewOrEda =
    artifact.artifact_key.toLowerCase().includes("summary") ||
    artifact.artifact_key.toLowerCase().includes("eda") ||
    artifact.title.toLowerCase().includes("eda") ||
    artifact.title.toLowerCase().includes("overview");

  const [showLiveDashboard, setShowLiveDashboard] = useState(isOverviewOrEda);

  useEffect(() => {
    setText(String(artifact.content.text ?? JSON.stringify(artifact.content, null, 2)));
  }, [artifact.id, artifact.version]);

  async function save() {
    setSaving(true);
    try {
      const saved = await ResearchAgent.saveArtifact(threadId, artifact.artifact_key, {
        title: artifact.title,
        kind: artifact.kind,
        content: {...artifact.content, text},
        created_by: "user",
      });
      onSaved(saved);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4">
      {isOverviewOrEda && !editing ? (
        <div className="flex items-center justify-between gap-2 rounded-xl border border-biu-sky/25 bg-biu-sky/[0.06] px-3 py-2">
          <span className="text-xs font-medium text-ink">
            Interactive Corpus Visualizations &amp; Report
          </span>
          <button
            type="button"
            onClick={() => setShowLiveDashboard((v) => !v)}
            className="rounded-lg border border-biu-sky/40 bg-biu-sky/15 px-2.5 py-1 text-xs font-medium text-biu-sky hover:bg-biu-sky/25 transition-colors"
          >
            {showLiveDashboard ? "Hide Live Charts" : "Show Live Charts"}
          </button>
        </div>
      ) : null}

      {isOverviewOrEda && showLiveDashboard && !editing ? (
        <Suspense fallback={<p className="muted text-sm">Loading visual charts…</p>}>
          <EdaDashboardPanel projectId={projectId} />
        </Suspense>
      ) : null}

      {editing ? (
        <>
          <label className="sr-only" htmlFor="canvas-editor">Canvas text</label>
          <textarea
            id="canvas-editor"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={18}
            className="w-full rounded-xl bg-white/5 border border-white/10 px-3.5 py-3 text-sm text-ink font-mono"
          />
        </>
      ) : (
        <div
          data-testid="canvas-markdown"
          className="rounded-2xl bg-white/[0.03] border border-white/10 p-4 text-sm text-ink space-y-3"
        >
          <ResearchMarkdown text={text} variant="canvas" />
        </div>
      )}

      <div className="flex items-center justify-between text-xs pt-1">
        <span className="muted">
          Version {artifact.version} · {editing ? "edit the text, then save" : "visual report + markdown"}
        </span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setEditing((value) => !value)}
            className="px-3 py-1 rounded-lg border border-white/10 text-ink hover:bg-white/5 transition-colors"
          >
            {editing ? "Preview" : "Edit"}
          </button>
          {editing ? (
            <button
              type="button"
              onClick={() => {
                void save().then(() => setEditing(false));
              }}
              disabled={saving}
              className="px-3 py-1 rounded-lg bg-biu-sky/20 text-biu-sky hover:bg-biu-sky/30 transition-colors"
            >
              Save
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
