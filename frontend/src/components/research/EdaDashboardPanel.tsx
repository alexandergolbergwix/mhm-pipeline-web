import {useEffect, useMemo, useState} from "react";
import {
  researchApi,
  type CoOccurrenceGraph,
  type CorpusMovement,
  type EntityKind,
  type PeopleNetwork,
  type PlaceRow,
  type ResearchSummary,
} from "@/api/research";
import {
  CHART_PALETTE,
  SignalAreaChart,
  SignalBarList,
  SignalColumnChart,
  SignalDonutChart,
  SignalFunnel,
  SignalStatGrid,
  type SignalBarItem,
  type SignalStatItem,
} from "./ResearchCharts";

const SOURCE_LABELS: Record<"rdf" | "wikibase" | "wikidata", string> = {
  rdf: "RDF",
  wikidata: "Wikidata",
  wikibase: "Wikibase",
};

function centuryLabel(year: number): string {
  const c = Math.floor((year - 1) / 100) + 1;
  const suffix = c === 11 || c === 12 || c === 13 ? "th" : c % 10 === 1 ? "st" : c % 10 === 2 ? "nd" : c % 10 === 3 ? "rd" : "th";
  return `${c}${suffix} c.`;
}

export default function EdaDashboardPanel({
  projectId,
  initialSummary,
}: {
  projectId: string;
  initialSummary?: ResearchSummary | null;
}) {
  const [summary, setSummary] = useState<ResearchSummary | null>(initialSummary ?? null);
  const [places, setPlaces] = useState<PlaceRow[]>([]);
  const [movement, setMovement] = useState<CorpusMovement | null>(null);
  const [coOccurrence, setCoOccurrence] = useState<CoOccurrenceGraph | null>(null);
  const [network, setNetwork] = useState<PeopleNetwork | null>(null);
  const [loading, setLoading] = useState(!initialSummary);

  const [timeMode, setTimeMode] = useState<"century" | "quarter">("century");
  const [peopleTab, setPeopleTab] = useState<"roles" | "top">("roles");
  const [placeFilter, setPlaceFilter] = useState<"all" | "production" | "mentioned">("production");

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    setLoading(true);

    researchApi
      .summary(projectId)
      .then((res) => {
        if (!cancelled) {
          setSummary(res);
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
      });

    researchApi
      .geography(projectId)
      .then((res) => {
        if (!cancelled && Array.isArray(res)) setPlaces(res);
      })
      .catch(() => {});

    researchApi
      .movement(projectId)
      .then((res) => {
        if (!cancelled && res) setMovement(res);
      })
      .catch(() => {});

    researchApi
      .coOccurrence(projectId)
      .then((res) => {
        if (!cancelled && res) setCoOccurrence(res);
      })
      .catch(() => {});

    researchApi
      .peopleNetwork(projectId)
      .then((res) => {
        if (!cancelled && res) setNetwork(res);
      })
      .catch(() => {});

    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const statItems = useMemo<SignalStatItem[]>(() => {
    if (!summary) return [];
    const buildBreakdown = (kind: EntityKind) => {
      const agg = summary.by_type?.[kind];
      if (!agg) return undefined;
      const avail = summary.sources_available;
      return (["rdf", "wikidata", "wikibase"] as const).map((s) => ({
        label: SOURCE_LABELS[s],
        value: avail && !avail[s] ? "—" : agg.by_source[s],
        muted: avail ? !avail[s] : false,
      }));
    };
    const triples = summary.triples ?? 0;
    return [
      {
        id: "manuscripts",
        label: "Manuscripts",
        value: summary.by_type?.manuscript?.total ?? summary.total_manuscripts,
        color: "#77cce5",
        accent: true,
        breakdown: buildBreakdown("manuscript"),
      },
      {
        id: "works",
        label: "Works",
        value: summary.by_type?.work?.total ?? summary.total_works,
        color: "#a78bfa",
        breakdown: buildBreakdown("work"),
      },
      {
        id: "persons",
        label: "Persons",
        value: summary.by_type?.person?.total ?? summary.total_persons,
        color: "#34d399",
        breakdown: buildBreakdown("person"),
      },
      {
        id: "places",
        label: "Places",
        value: summary.by_type?.place?.total ?? summary.total_places,
        color: "#f59e0b",
        breakdown: buildBreakdown("place"),
      },
      {
        id: "triples",
        label: "RDF Triples",
        value: triples,
        color: "#60a5fa",
        note: "Merged semantic graph",
      },
    ];
  }, [summary]);

  const roleItems = useMemo<SignalBarItem[]>(() => {
    if (!summary?.persons_by_role) return [];
    return [
      {key: "scribe", label: "Scribes", value: summary.persons_by_role.scribe || 0, color: "#77cce5"},
      {key: "author", label: "Authors", value: summary.persons_by_role.author || 0, color: "#a78bfa"},
      {key: "owner", label: "Owners", value: summary.persons_by_role.owner || 0, color: "#34d399"},
    ].filter((r) => r.value > 0);
  }, [summary]);

  const topPeopleItems = useMemo<SignalBarItem[]>(() => {
    if (!network?.nodes?.length) return [];
    const roleColor: Record<string, string> = {
      scribe: "#77cce5",
      author: "#a78bfa",
      owner: "#34d399",
    };
    return [...network.nodes]
      .sort((a, b) => b.ms_count - a.ms_count)
      .slice(0, 10)
      .map((n) => ({
        key: n.id,
        label: n.label,
        value: n.ms_count,
        badge: n.role,
        color: roleColor[n.role] ?? "#77cce5",
      }));
  }, [network]);

  const timeSeries = useMemo(() => {
    const yc = movement?.year_counts ?? [];
    if (!yc.length) return {labels: [] as string[], values: [] as number[], peakLabel: "", datedTotal: 0};
    const buckets = new Map<number, {label: string; count: number}>();
    let datedTotal = 0;
    for (const row of yc) {
      if (!row.year || row.year < 800 || row.year > 2050) continue;
      datedTotal += row.count;
      if (timeMode === "century") {
        const c = Math.floor((row.year - 1) / 100) + 1;
        const prev = buckets.get(c) ?? {label: centuryLabel(row.year), count: 0};
        prev.count += row.count;
        buckets.set(c, prev);
      } else {
        const q = Math.floor(row.year / 25) * 25;
        const prev = buckets.get(q) ?? {label: `${q}s`, count: 0};
        prev.count += row.count;
        buckets.set(q, prev);
      }
    }
    const sorted = [...buckets.entries()].sort((a, b) => a[0] - b[0]);
    let peakLabel = "";
    let peakCount = 0;
    for (const [, b] of sorted) {
      if (b.count > peakCount) {
        peakCount = b.count;
        peakLabel = `${b.label} (${b.count} mss)`;
      }
    }
    return {
      labels: sorted.map(([, b]) => b.label),
      values: sorted.map(([, b]) => b.count),
      peakLabel,
      datedTotal,
    };
  }, [movement, timeMode]);

  const placeBarItems = useMemo<SignalBarItem[]>(() => {
    if (!places.length) return [];
    const filtered =
      placeFilter === "all"
        ? places
        : places.filter((p) => p.type === placeFilter);
    const list = filtered.length > 0 ? filtered : places;
    return [...list]
      .sort((a, b) => b.ms_count - a.ms_count)
      .slice(0, 12)
      .map((p, idx) => ({
        key: `${p.place}-${p.type}`,
        label: p.place_label,
        value: p.ms_count,
        badge: p.lat !== null ? `${p.type} · ✓ geo` : p.type,
        color: p.type === "production" ? "#77cce5" : "#fb923c",
        secondaryValue: undefined,
        href: p.place.startsWith("http") ? p.place : undefined,
        ...(idx < 3 ? {} : {}),
      }));
  }, [places, placeFilter]);

  const topWorkItems = useMemo<SignalBarItem[]>(() => {
    if (!coOccurrence?.nodes?.length) return [];
    return [...coOccurrence.nodes]
      .sort((a, b) => b.degree - a.degree)
      .slice(0, 10)
      .map((w, idx) => ({
        key: w.id,
        label: w.label,
        value: w.degree,
        color: CHART_PALETTE[(idx + 1) % CHART_PALETTE.length],
      }));
  }, [coOccurrence]);

  const coverageSteps = useMemo(() => {
    if (!summary) return [];
    const totalMs = summary.total_manuscripts || 0;
    if (totalMs <= 0) return [];
    const datedMs = movement?.manuscripts?.filter(
      (m) => m.production_year !== null || m.production_year_earliest !== null,
    ).length ?? timeSeries.datedTotal;
    const geoMs = movement?.manuscripts?.filter((m) => m.has_production_point).length ?? 0;
    const wikidataMs = summary.by_type?.manuscript?.by_source?.wikidata ?? 0;
    const steps = [{label: "Total Corpus Manuscripts", value: totalMs}];
    if (datedMs > 0) {
      steps.push({label: "Dated Production (Chronology)", value: Math.min(totalMs, datedMs)});
    }
    if (geoMs > 0) {
      steps.push({label: "Georeferenced Production Places (KIMA)", value: Math.min(totalMs, geoMs)});
    }
    if (wikidataMs > 0) {
      steps.push({label: "Linked on Public Wikidata", value: Math.min(totalMs, wikidataMs)});
    }
    return steps.length >= 2 ? steps : [];
  }, [summary, movement, timeSeries.datedTotal]);

  if (loading && !summary) {
    return (
      <div className="space-y-4 p-2">
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          {[1, 2, 3, 4, 5].map((n) => (
            <div key={n} className="h-24 rounded-2xl bg-white/5 animate-pulse border border-white/10" />
          ))}
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="h-64 rounded-2xl bg-white/5 animate-pulse border border-white/10" />
          <div className="h-64 rounded-2xl bg-white/5 animate-pulse border border-white/10" />
        </div>
      </div>
    );
  }

  if (!summary) {
    return (
      <div className="rounded-2xl border border-white/10 bg-[var(--surface-inset)] p-8 text-center text-sm text-muted">
        No corpus summary available yet. Build the RDF graph for this run first.
      </div>
    );
  }

  return (
    <div className="space-y-4" data-testid="eda-dashboard-panel">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 pb-2.5">
        <div>
          <div className="kicker">Corpus Intelligence &amp; Exploratory Data Analysis</div>
          <h3 className="text-base font-semibold text-ink">
            Multi-Source Signal Overview (RDF · Wikibase · Wikidata)
          </h3>
        </div>
        {timeSeries.peakLabel ? (
          <span className="rounded-full border border-biu-sky/35 bg-biu-sky/10 px-3 py-1 text-xs text-biu-sky font-medium">
            Peak production: {timeSeries.peakLabel}
          </span>
        ) : null}
      </div>

      <SignalStatGrid items={statItems} cols={5} />

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="rounded-2xl border border-white/10 bg-[var(--surface-inset)] p-4 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h4 className="text-sm font-semibold text-ink">Chronological Distribution</h4>
              <p className="text-xs text-muted">
                {timeSeries.datedTotal > 0
                  ? `${timeSeries.datedTotal.toLocaleString()} dated manuscripts across time`
                  : "Manuscript production timeline"}
              </p>
            </div>
            <div className="flex rounded-lg border border-white/10 bg-white/[0.03] p-0.5 text-[11px]">
              <button
                type="button"
                onClick={() => setTimeMode("century")}
                className={`px-2.5 py-0.5 rounded-md transition-colors ${
                  timeMode === "century" ? "bg-biu-sky/20 text-biu-sky font-medium" : "text-muted hover:text-ink"
                }`}
              >
                By Century
              </button>
              <button
                type="button"
                onClick={() => setTimeMode("quarter")}
                className={`px-2.5 py-0.5 rounded-md transition-colors ${
                  timeMode === "quarter" ? "bg-biu-sky/20 text-biu-sky font-medium" : "text-muted hover:text-ink"
                }`}
              >
                25-Yr Trend
              </button>
            </div>
          </div>

          {timeSeries.labels.length > 0 ? (
            timeMode === "century" ? (
              <SignalColumnChart
                labels={timeSeries.labels}
                series={[{name: "Manuscripts", values: timeSeries.values, color: "#77cce5"}]}
                unit="mss"
                height={200}
              />
            ) : (
              <SignalAreaChart
                labels={timeSeries.labels}
                series={[{name: "Manuscripts", values: timeSeries.values, color: "#77cce5"}]}
                unit="mss"
                height={200}
              />
            )
          ) : (
            <div className="py-12 text-center text-xs text-muted">
              No dated production records available in the movement index yet.
            </div>
          )}
        </div>

        <div className="rounded-2xl border border-white/10 bg-[var(--surface-inset)] p-4 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h4 className="text-sm font-semibold text-ink">People &amp; Historical Roles</h4>
              <p className="text-xs text-muted">
                Scribes, authors, and owners attested across the corpus
              </p>
            </div>
            {topPeopleItems.length > 0 ? (
              <div className="flex rounded-lg border border-white/10 bg-white/[0.03] p-0.5 text-[11px]">
                <button
                  type="button"
                  onClick={() => setPeopleTab("roles")}
                  className={`px-2.5 py-0.5 rounded-md transition-colors ${
                    peopleTab === "roles" ? "bg-biu-sky/20 text-biu-sky font-medium" : "text-muted hover:text-ink"
                  }`}
                >
                  Role Share
                </button>
                <button
                  type="button"
                  onClick={() => setPeopleTab("top")}
                  className={`px-2.5 py-0.5 rounded-md transition-colors ${
                    peopleTab === "top" ? "bg-biu-sky/20 text-biu-sky font-medium" : "text-muted hover:text-ink"
                  }`}
                >
                  Top Figures
                </button>
              </div>
            ) : null}
          </div>

          {peopleTab === "top" && topPeopleItems.length > 0 ? (
            <SignalBarList items={topPeopleItems} unit="mss" maxItems={6} showShare={false} />
          ) : roleItems.length > 0 ? (
            <div className="space-y-3 pt-1">
              <SignalDonutChart items={roleItems} unit="people" centerLabel="role links" />
              <SignalBarList items={roleItems} unit="people" showRank={false} />
            </div>
          ) : (
            <div className="py-12 text-center text-xs text-muted">
              No role attributions recorded in this graph.
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="rounded-2xl border border-white/10 bg-[var(--surface-inset)] p-4 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h4 className="text-sm font-semibold text-ink">Top Geographic Centers</h4>
              <p className="text-xs text-muted">
                {places.length > 0
                  ? `${places.length.toLocaleString()} distinct places in the corpus`
                  : "Production sites and mentioned places"}
              </p>
            </div>
            <div className="flex rounded-lg border border-white/10 bg-white/[0.03] p-0.5 text-[11px]">
              {(
                [
                  {id: "production", label: "Production"},
                  {id: "mentioned", label: "Mentioned"},
                  {id: "all", label: "All"},
                ] as const
              ).map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setPlaceFilter(tab.id)}
                  className={`px-2 py-0.5 rounded-md transition-colors ${
                    placeFilter === tab.id ? "bg-biu-sky/20 text-biu-sky font-medium" : "text-muted hover:text-ink"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>

          {placeBarItems.length > 0 ? (
            <SignalBarList items={placeBarItems} unit="mss" maxItems={8} />
          ) : (
            <div className="py-10 text-center text-xs text-muted">
              No geographic places found in this run&rsquo;s graph.
            </div>
          )}
        </div>

        <div className="space-y-4">
          {topWorkItems.length > 0 ? (
            <div className="rounded-2xl border border-white/10 bg-[var(--surface-inset)] p-4 space-y-3">
              <div>
                <h4 className="text-sm font-semibold text-ink">Most Co-Occurring Works</h4>
                <p className="text-xs text-muted">
                  Works most frequently bound together in multi-text manuscripts
                </p>
              </div>
              <SignalBarList items={topWorkItems} unit="co-occurrences" maxItems={6} showShare={false} />
            </div>
          ) : null}

          {coverageSteps.length > 0 ? (
            <div className="rounded-2xl border border-white/10 bg-[var(--surface-inset)] p-4 space-y-3">
              <div>
                <h4 className="text-sm font-semibold text-ink">Corpus Signal &amp; Enrichment Funnel</h4>
                <p className="text-xs text-muted">
                  Share of manuscripts carrying dated, georeferenced, and linked-data signals
                </p>
              </div>
              <SignalFunnel steps={coverageSteps} unit="mss" />
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
