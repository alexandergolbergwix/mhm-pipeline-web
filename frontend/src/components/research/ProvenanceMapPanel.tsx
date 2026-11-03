/**
 * Provenance Movement Map — Phase 1 (single manuscript) + Phase 2 (corpus).
 *
 * A mode toggle switches between:
 *   - "Single": pick one manuscript, see its full provenance chain (Phase 1).
 *   - "Corpus": all manuscripts on one map, filterable by year / place /
 *     genre / owner, animated by year (Phase 2).
 *
 * Phase-1 integrity guarantees are unchanged.  Phase-2 shows only
 * production→current-holder arcs; only manuscripts with KIMA production
 * coordinates get a production point on the corpus map.
 */
import {useCallback, useEffect, useMemo, useRef, useState} from "react";
import {
  CircleMarker,
  MapContainer,
  Polyline,
  Popup,
  TileLayer,
  useMap,
} from "react-leaflet";
import "leaflet/dist/leaflet.css";
import {
  type CorpusManuscript,
  type CorpusMovementFacets,
  type CorpusMovementFilters,
  type ManuscriptPick,
  type MapStop,
  type ProvenanceMap,
  researchApi,
} from "@/api/research";
import {mappableStops, quadraticArcPoints, stopRevealedAt, timeBounds} from "./provenanceArc";

// ── colours ──────────────────────────────────────────────────────────────

const KIND_COLOR: Record<string, string> = {
  production:        "#8b5cf6",
  owner:             "#38bdf8",
  significant_place: "#f59e0b",
  acquisition:       "#ec4899",
  conservation:      "#14b8a6",
  exhibition:        "#a855f7",
  current_holder:    "#22c55e",
};

const KIND_LABEL: Record<string, string> = {
  production:        "Production",
  owner:             "Owner (inferred location)",
  significant_place: "Significant place",
  acquisition:       "Acquisition (MARC 541)",
  conservation:      "Conservation (MARC 583)",
  exhibition:        "Exhibition (MARC 583)",
  current_holder:    "Current holder",
};

// ── shared helpers ────────────────────────────────────────────────────────

function FitBounds({stops}: {stops: MapStop[]}) {
  const map = useMap();
  useEffect(() => {
    const pts = mappableStops(stops).map(
      (s) => [s.lat as number, s.lon as number] as [number, number],
    );
    if (pts.length === 1) map.setView(pts[0], 5);
    else if (pts.length > 1) map.fitBounds(pts, {padding: [40, 40]});
  }, [stops, map]);
  return null;
}

function FitBoundsLatLon({points}: {points: [number, number][]}) {
  const map = useMap();
  useEffect(() => {
    if (points.length === 1) map.setView(points[0], 5);
    else if (points.length > 1) map.fitBounds(points, {padding: [40, 40]});
  }, [points, map]);
  return null;
}

function yearText(s: MapStop): string {
  if (s.is_present) return "present";
  if (s.year != null) return String(s.year);
  if (s.year_earliest != null && s.year_latest != null)
    return s.year_earliest === s.year_latest
      ? String(s.year_earliest)
      : `${s.year_earliest}–${s.year_latest}`;
  if (s.birth_year != null) return `b. ${s.birth_year}`;
  return "undated";
}

// ── Mode toggle ───────────────────────────────────────────────────────────

type Mode = "single" | "corpus";

function ModeToggle({mode, onChange}: {mode: Mode; onChange: (m: Mode) => void}) {
  return (
    <div className="flex rounded-lg border border-white/10 overflow-hidden text-sm">
      {(["single", "corpus"] as Mode[]).map((m) => (
        <button
          key={m}
          onClick={() => onChange(m)}
          className={`px-4 py-1.5 transition-colors ${
            mode === m
              ? "bg-biu-sky/20 text-biu-sky"
              : "text-muted hover:text-ink"
          }`}
        >
          {m === "single" ? "Single manuscript" : "Corpus view"}
        </button>
      ))}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// Phase-1: single-manuscript view (unchanged from original)
// ═══════════════════════════════════════════════════════════════════════════

function SingleManuscriptView({
  projectId,
  list,
  initialCn,
}: {
  projectId: string;
  list: ManuscriptPick[];
  initialCn?: string;
}) {
  const [filter, setFilter] = useState("");
  const [cn, setCn] = useState<string>(initialCn ?? "");

  // A canvas map artifact names its manuscript — follow it (agent-placed maps).
  useEffect(() => {
    if (initialCn) setCn(initialCn);
  }, [initialCn]);
  const [data, setData] = useState<ProvenanceMap | null>(null);
  const [loading, setLoading] = useState(false);
  const [includeUnapproved, setIncludeUnapproved] = useState(false);
  const [year, setYear] = useState<number>(0);
  const [playing, setPlaying] = useState(false);
  const playRef = useRef<number | null>(null);

  useEffect(() => {
    if (!cn) { setData(null); return; }
    let cancelled = false;
    setLoading(true);
    researchApi
      .provenanceMap(projectId, cn, includeUnapproved)
      .then((m) => { if (!cancelled) setData(m); })
      .catch(() => { if (!cancelled) setData(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [projectId, cn, includeUnapproved]);

  const bounds = useMemo(() => (data ? timeBounds(data.stops) : null), [data]);
  const maxTime = bounds?.max ?? 0;

  useEffect(() => { if (bounds) setYear(bounds.max); }, [bounds]);

  useEffect(() => {
    if (!playing || !bounds) return;
    if (year >= bounds.max) { setPlaying(false); return; }
    playRef.current = window.setTimeout(() => {
      setYear((y) => Math.min(bounds.max, y + Math.max(1, Math.round((bounds.max - bounds.min) / 40))));
    }, 250);
    return () => { if (playRef.current) window.clearTimeout(playRef.current); };
  }, [playing, year, bounds]);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return list;
    return list.filter(
      (m) => (m.label ?? "").toLowerCase().includes(q) || m.control_number.includes(q),
    );
  }, [list, filter]);

  const stops = data?.stops ?? [];
  const revealed = useMemo(
    () => stops.map((s) => stopRevealedAt(s, year, maxTime)),
    [stops, year, maxTime],
  );

  const arcs = useMemo(() => {
    if (!data) return [];
    return data.edges
      .filter((e) => revealed[e.from] && revealed[e.to])
      .map((e) => {
        const a = stops[e.from];
        const b = stops[e.to];
        if (a.lat == null || b.lat == null) return null;
        return {
          points: quadraticArcPoints(
            [a.lat, a.lon as number],
            [b.lat, b.lon as number],
          ),
          inferred: e.inferred,
        };
      })
      .filter(
        (x): x is {points: [number, number][]; inferred: boolean} => x !== null,
      );
  }, [data, revealed, stops]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter manuscripts…"
          aria-label="Filter manuscripts"
          className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-sm text-ink w-56"
        />
        <select
          value={cn}
          onChange={(e) => setCn(e.target.value)}
          aria-label="Select manuscript"
          className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-sm text-ink flex-1 min-w-[16rem]"
        >
          <option value="">— select a manuscript —</option>
          {filtered.map((m) => (
            <option key={m.control_number} value={m.control_number}>
              {(m.label ?? m.control_number)}
              {m.production_year != null ? ` · ${m.production_year}` : ""}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 text-xs text-muted">
          <input
            type="checkbox"
            checked={includeUnapproved}
            onChange={(e) => setIncludeUnapproved(e.target.checked)}
          />
          Preview unapproved owners
        </label>
      </div>

      {loading && (
        <div className="muted text-sm py-6 text-center">Loading map…</div>
      )}

      {data && !loading && (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <div
              className="lg:col-span-2 rounded-xl overflow-hidden border border-white/10"
              data-testid="prov-map"
              style={{height: 460}}
            >
              <MapContainer
                center={[31.5, 35]}
                zoom={3}
                style={{height: "100%", width: "100%"}}
                scrollWheelZoom
              >
                <TileLayer
                  attribution="&copy; OpenStreetMap contributors"
                  url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                />
                <FitBounds stops={stops} />
                {arcs.map((arc, i) => (
                  <Polyline
                    key={`arc-${i}`}
                    positions={arc.points}
                    pathOptions={{
                      color: arc.inferred ? "#94a3b8" : "#22c55e",
                      weight: 2,
                      opacity: 0.8,
                      dashArray: arc.inferred ? "6 6" : undefined,
                    }}
                  />
                ))}
                {stops.map((s, i) =>
                  s.has_point && s.lat != null && revealed[i] ? (
                    <CircleMarker
                      key={`stop-${i}`}
                      center={[s.lat, s.lon as number]}
                      radius={
                        s.kind === "production" || s.kind === "current_holder"
                          ? 9
                          : 7
                      }
                      pathOptions={{
                        color: KIND_COLOR[s.kind] ?? "#cbd5e1",
                        fillColor: KIND_COLOR[s.kind] ?? "#cbd5e1",
                        fillOpacity: s.inferred_geo ? 0.4 : 0.8,
                        weight: s.inferred_geo ? 1 : 2,
                        dashArray: s.inferred_geo ? "3 3" : undefined,
                      }}
                    >
                      <Popup>
                        <div className="text-xs space-y-0.5">
                          <div className="font-semibold">{s.label}</div>
                          <div>
                            {KIND_LABEL[s.kind] ?? s.kind} · {yearText(s)}
                          </div>
                          {s.inferred_geo && (
                            <div className="text-amber-600">
                              inferred location
                              {s.geo_source_label
                                ? ` (via ${s.geo_source_label})`
                                : ""}
                            </div>
                          )}
                        </div>
                      </Popup>
                    </CircleMarker>
                  ) : null,
                )}
              </MapContainer>
            </div>

            <div className="space-y-3">
              <Legend />
              <div className="rounded-xl border border-white/10 p-3">
                <div className="kicker mb-2">Sequence</div>
                <ol className="space-y-1.5">
                  {stops.map((s, i) => (
                    <li
                      key={`t-${i}`}
                      className={`flex items-center gap-2 text-sm transition-opacity ${
                        revealed[i] ? "opacity-100" : "opacity-30"
                      }`}
                    >
                      <span
                        className="inline-block w-2.5 h-2.5 rounded-full shrink-0"
                        style={{background: KIND_COLOR[s.kind] ?? "#cbd5e1"}}
                      />
                      <span className="text-ink truncate">{s.label}</span>
                      <span className="text-muted text-xs ml-auto shrink-0">
                        {yearText(s)}
                      </span>
                      {s.inferred_geo && (
                        <span className="text-[10px] px-1 rounded bg-amber-500/20 text-amber-400 shrink-0">
                          inferred
                        </span>
                      )}
                    </li>
                  ))}
                </ol>
              </div>

              {data.dropped.length > 0 && (
                <div className="rounded-xl border border-white/10 p-3">
                  <div className="kicker mb-2">
                    Not mapped ({data.dropped.length})
                  </div>
                  <ul className="space-y-1 text-xs">
                    {data.dropped.map((d, i) => (
                      <li
                        key={`d-${i}`}
                        className="flex justify-between gap-2"
                      >
                        <span className="text-ink truncate">{d.label}</span>
                        <span className="text-muted shrink-0">
                          {d.reason.replace(/_/g, " ")}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>

          {bounds && bounds.max > bounds.min && (
            <div className="flex items-center gap-3 rounded-xl border border-white/10 px-4 py-3">
              <button
                onClick={() => {
                  if (playing) { setPlaying(false); return; }
                  if (year >= bounds.max) setYear(bounds.min);
                  setPlaying(true);
                }}
                className="text-sm px-3 py-1 rounded-lg bg-biu-sky/15 text-biu-sky border border-biu-sky/30"
              >
                {playing ? "⏸ Pause" : "▶ Play"}
              </button>
              <span className="text-xs text-muted tabular-nums w-12 text-right">
                {bounds.min}
              </span>
              <input
                type="range"
                min={bounds.min}
                max={bounds.max}
                value={year}
                onChange={(e) => {
                  setPlaying(false);
                  setYear(Number(e.target.value));
                }}
                aria-label="Year"
                className="flex-1"
              />
              <span className="text-xs text-muted tabular-nums w-12">
                {bounds.max}
              </span>
              <span className="text-sm text-ink tabular-nums w-14 text-right">
                {year}
              </span>
            </div>
          )}
        </>
      )}

      {!cn && !loading && (
        <div className="muted text-sm py-10 text-center">
          Select a manuscript to see its provenance movement on the map.
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// Phase-2: corpus view
// ═══════════════════════════════════════════════════════════════════════════

const NLI_LAT = 31.7942;
const NLI_LON = 35.2007;

type CorpusMovementData = {
  manuscripts: CorpusManuscript[];
  year_counts: {year: number; count: number}[];
};

function useCorpusData(projectId: string, filters: CorpusMovementFilters) {
  const [data, setData] = useState<CorpusMovementData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryTick, setRetryTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: number | null = null;
    setLoading(true);
    setError(null);

    const load = (attempt: number) => {
      researchApi
        .movement(projectId, filters)
        .then((d) => {
          if (!cancelled) {
            setData(d);
            setError(null);
            setLoading(false);
          }
        })
        .catch((err) => {
          if (cancelled) return;
          if (attempt < 1) {
            timer = window.setTimeout(() => load(attempt + 1), 1500);
            return;
          }
          setData(null);
          setError(err instanceof Error ? err.message : "Could not load the corpus movement map.");
          setLoading(false);
        });
    };

    load(0);
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    projectId,
    filters.from_year,
    filters.to_year,
    filters.place,
    filters.genre,
    filters.owner,
    filters.holder,
    retryTick,
  ]);

  const reload = useCallback(() => setRetryTick((t) => t + 1), []);
  return {data, loading, error, reload};
}

function CorpusFilterBar({
  facets,
  filters,
  onChange,
}: {
  facets: CorpusMovementFacets | null;
  filters: CorpusMovementFilters;
  onChange: (f: CorpusMovementFilters) => void;
}) {
  const yearMin = facets?.year_min ?? 1200;
  const yearMax = facets?.year_max ?? 1900;

  const [localFrom, setLocalFrom] = useState<string>("");
  const [localTo, setLocalTo]   = useState<string>("");

  // Sync when facets arrive.
  useEffect(() => {
    setLocalFrom(filters.from_year != null ? String(filters.from_year) : "");
    setLocalTo(filters.to_year   != null ? String(filters.to_year)   : "");
  }, [filters.from_year, filters.to_year]);

  const commitYear = useCallback(() => {
    const from = localFrom ? parseInt(localFrom) : undefined;
    const to   = localTo   ? parseInt(localTo)   : undefined;
    onChange({...filters, from_year: from, to_year: to});
  }, [localFrom, localTo, filters, onChange]);

  return (
    <div
      className="flex flex-wrap items-end gap-3 rounded-xl border border-white/10 px-4 py-3 text-sm"
      data-testid="corpus-filter-bar"
    >
      {/* Year range */}
      <div className="flex flex-col gap-1">
        <span className="text-[10px] text-muted uppercase tracking-wide">Year range</span>
        <div className="flex items-center gap-1.5">
          <input
            type="number"
            value={localFrom}
            min={yearMin}
            max={yearMax}
            placeholder={String(yearMin)}
            aria-label="From year"
            className="w-20 rounded border border-white/10 bg-white/5 px-2 py-1 text-xs text-ink"
            onChange={(e) => setLocalFrom(e.target.value)}
            onBlur={commitYear}
            onKeyDown={(e) => { if (e.key === "Enter") commitYear(); }}
          />
          <span className="text-muted">–</span>
          <input
            type="number"
            value={localTo}
            min={yearMin}
            max={yearMax}
            placeholder={String(yearMax)}
            aria-label="To year"
            className="w-20 rounded border border-white/10 bg-white/5 px-2 py-1 text-xs text-ink"
            onChange={(e) => setLocalTo(e.target.value)}
            onBlur={commitYear}
            onKeyDown={(e) => { if (e.key === "Enter") commitYear(); }}
          />
        </div>
      </div>

      {/* Place */}
      <FilterSelect
        label="Place"
        options={facets?.places ?? []}
        value={filters.place ?? ""}
        onChange={(v) => onChange({...filters, place: v || undefined})}
        placeholder="All places"
      />

      {/* Genre */}
      <FilterSelect
        label="Genre"
        options={facets?.genres ?? []}
        value={filters.genre ?? ""}
        onChange={(v) => onChange({...filters, genre: v || undefined})}
        placeholder="All genres"
      />

      {/* Owner */}
      <FilterSelect
        label="Owner"
        options={facets?.owners ?? []}
        value={filters.owner ?? ""}
        onChange={(v) => onChange({...filters, owner: v || undefined})}
        placeholder="All owners"
      />

      {/* Current Holder */}
      {facets?.holders && facets.holders.length > 0 ? (
        <FilterSelect
          label="Current holder"
          options={facets.holders}
          value={filters.holder ?? ""}
          onChange={(v) => onChange({...filters, holder: v || undefined})}
          placeholder="All holders"
        />
      ) : null}

      {/* Clear */}
      {(filters.from_year != null || filters.to_year != null ||
        filters.place || filters.genre || filters.owner || filters.holder) && (
        <button
          onClick={() => {
            setLocalFrom("");
            setLocalTo("");
            onChange({});
          }}
          className="text-xs text-muted hover:text-ink px-2 py-1 rounded border border-white/10"
        >
          Clear filters
        </button>
      )}
    </div>
  );
}

function FilterSelect({
  label,
  options,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  options: string[];
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[10px] text-muted uppercase tracking-wide">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label={label}
        className="rounded border border-white/10 bg-white/5 px-2 py-1 text-xs text-ink min-w-[120px]"
      >
        <option value="">{placeholder}</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </div>
  );
}

function CorpusYearSlider({
  yearCounts,
  year,
  onYearChange,
  playing,
  onPlayPause,
}: {
  yearCounts: {year: number; count: number}[];
  year: number;
  onYearChange: (y: number) => void;
  playing: boolean;
  onPlayPause: () => void;
}) {
  if (yearCounts.length === 0) return null;
  const minY = yearCounts[0].year;
  const maxY = yearCounts[yearCounts.length - 1].year;
  if (minY === maxY) return null;
  const matchCount = yearCounts
    .filter((c) => c.year <= year)
    .reduce((s, c) => s + c.count, 0);
  const total = yearCounts.reduce((s, c) => s + c.count, 0);

  return (
    <div className="flex items-center gap-3 rounded-xl border border-white/10 px-4 py-3">
      <button
        onClick={onPlayPause}
        className="text-sm px-3 py-1 rounded-lg bg-biu-sky/15 text-biu-sky border border-biu-sky/30"
      >
        {playing ? "⏸ Pause" : "▶ Play"}
      </button>
      <span className="text-xs text-muted tabular-nums w-12 text-right">{minY}</span>
      <input
        type="range"
        min={minY}
        max={maxY}
        value={year}
        onChange={(e) => onYearChange(Number(e.target.value))}
        aria-label="Year"
        className="flex-1"
      />
      <span className="text-xs text-muted tabular-nums w-12">{maxY}</span>
      <span className="text-sm text-ink tabular-nums w-14 text-right">{year}</span>
      <span className="text-xs text-muted tabular-nums">
        {matchCount}/{total} MS
      </span>
    </div>
  );
}

function CorpusView({projectId}: {projectId: string}) {
  const [facets, setFacets] = useState<CorpusMovementFacets | null>(null);
  const [filters, setFilters] = useState<CorpusMovementFilters>({});
  const {data, loading, error, reload} = useCorpusData(projectId, filters);

  const [year, setYear] = useState<number>(0);
  const [playing, setPlaying] = useState(false);
  const playRef = useRef<number | null>(null);

  // Load facets once.
  useEffect(() => {
    let cancelled = false;
    researchApi.movementFacets(projectId).then((f) => {
      if (cancelled) return;
      setFacets(f);
      setYear(f.year_max ?? 1900);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [projectId]);

  // Play animation.
  useEffect(() => {
    if (!playing || !facets) return;
    const maxY = facets.year_max ?? 1900;
    if (year >= maxY) { setPlaying(false); return; }
    playRef.current = window.setTimeout(() => {
      const minY = facets.year_min ?? 1200;
      setYear((y) => Math.min(maxY, y + Math.max(1, Math.round((maxY - minY) / 60))));
    }, 200);
    return () => { if (playRef.current) window.clearTimeout(playRef.current); };
  }, [playing, year, facets]);

  // Manuscripts visible at the current year position.
  const visible = useMemo<CorpusManuscript[]>(() => {
    if (!data) return [];
    return data.manuscripts.filter((m) => {
      const y = m.production_year ?? m.production_year_latest ?? m.production_year_earliest;
      if (y == null) return true; // undated always visible
      return y <= year;
    });
  }, [data, year]);

  // Provenance path arcs + intermediate waypoints + current holder clusters.
  const {arcs, waypointClusters, holderClusters} = useMemo(() => {
    const arcList: Array<{key: string; points: [number, number][]; color: string}> = [];
    const wpMap = new Map<string, {lat: number; lon: number; label: string; kind: string; count: number; sampleMs: string[]}>();
    const hMap = new Map<string, {lat: number; lon: number; label: string; count: number; sampleMs: string[]}>();

    for (const m of visible) {
      const hLat = typeof m.holder_lat === "number" ? m.holder_lat : null;
      const hLon = typeof m.holder_lon === "number" ? m.holder_lon : null;
      const hLabel = m.holder_label || "National Library of Israel";
      if (hLat !== null && hLon !== null) {
        const hKey = `${hLat.toFixed(3)},${hLon.toFixed(3)}:${hLabel}`;
        const existingH = hMap.get(hKey) ?? {lat: hLat, lon: hLon, label: hLabel, count: 0, sampleMs: []};
        existingH.count += 1;
        if (existingH.sampleMs.length < 6) existingH.sampleMs.push(m.label);
        hMap.set(hKey, existingH);
      }

      const chain: Array<{lat: number; lon: number; kind: "production" | "waypoint" | "holder"}> = [];
      if (m.has_production_point && m.production_lat != null && m.production_lon != null) {
        chain.push({lat: m.production_lat, lon: m.production_lon, kind: "production"});
      }
      for (const ep of m.event_places ?? []) {
        if (typeof ep.lat === "number" && typeof ep.lon === "number") {
          const prev = chain[chain.length - 1];
          if (!prev || Math.abs(prev.lat - ep.lat) > 0.01 || Math.abs(prev.lon - ep.lon) > 0.01) {
            chain.push({lat: ep.lat, lon: ep.lon, kind: "waypoint"});
          }
          const wpKey = `${ep.lat.toFixed(3)},${ep.lon.toFixed(3)}:${ep.place}`;
          const existingWp = wpMap.get(wpKey) ?? {
            lat: ep.lat,
            lon: ep.lon,
            label: ep.place,
            kind: ep.type || "waypoint",
            count: 0,
            sampleMs: [],
          };
          existingWp.count += 1;
          if (existingWp.sampleMs.length < 6) existingWp.sampleMs.push(m.label);
          wpMap.set(wpKey, existingWp);
        }
      }
      if (hLat !== null && hLon !== null) {
        const prev = chain[chain.length - 1];
        if (!prev || Math.abs(prev.lat - hLat) > 0.01 || Math.abs(prev.lon - hLon) > 0.01) {
          chain.push({lat: hLat, lon: hLon, kind: "holder"});
        }
      }

      for (let i = 0; i < chain.length - 1; i += 1) {
        const a = chain[i];
        const b = chain[i + 1];
        const isWaypointStep = a.kind === "waypoint" || b.kind === "waypoint";
        arcList.push({
          key: `${m.control_number}-${i}`,
          points: quadraticArcPoints([a.lat, a.lon], [b.lat, b.lon]),
          color: isWaypointStep ? "#f59e0b" : "#8b5cf6",
        });
      }
    }

    return {
      arcs: arcList,
      waypointClusters: [...wpMap.values()].sort((a, b) => b.count - a.count),
      holderClusters: [...hMap.values()].sort((a, b) => b.count - a.count),
    };
  }, [visible]);

  const allPoints = useMemo<[number, number][]>(() => {
    const pts: [number, number][] = [];
    for (const m of visible) {
      if (m.has_production_point && m.production_lat != null && m.production_lon != null) {
        pts.push([m.production_lat, m.production_lon]);
      }
    }
    for (const wp of waypointClusters) {
      pts.push([wp.lat, wp.lon]);
    }
    for (const h of holderClusters) {
      pts.push([h.lat, h.lon]);
    }
    if (pts.length === 0) pts.push([NLI_LAT, NLI_LON]);
    return pts;
  }, [visible, waypointClusters, holderClusters]);

  const withPoint = visible.filter((m) => m.has_production_point);
  const withAnyTrajectory = visible.filter(
    (m) => m.has_production_point || (m.event_places ?? []).some((ep) => ep.lat != null),
  );
  const noPoint = visible.filter((m) => !m.has_production_point);

  return (
    <div className="space-y-4">
      <CorpusFilterBar facets={facets} filters={filters} onChange={setFilters} />

      {loading && (
        <div className="muted text-sm py-6 text-center">Loading corpus…</div>
      )}

      {!loading && data && (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
            {/* Map */}
            <div
              className="lg:col-span-3 rounded-xl overflow-hidden border border-white/10"
              data-testid="corpus-map"
              style={{height: 480}}
            >
              <MapContainer
                center={[31.5, 35]}
                zoom={3}
                style={{height: "100%", width: "100%"}}
                scrollWheelZoom
              >
                <TileLayer
                  attribution="&copy; OpenStreetMap contributors"
                  url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                />
                <FitBoundsLatLon points={allPoints} />

                {/* Provenance movement arcs (production → waypoints → current holder) */}
                {arcs.map((arc) => (
                  <Polyline
                    key={arc.key}
                    positions={arc.points}
                    pathOptions={{
                      color: arc.color,
                      weight: 1.5,
                      opacity: 0.5,
                      dashArray: "5 4",
                    }}
                  />
                ))}

                {/* Production place markers */}
                {withPoint.map((m) => (
                  <CircleMarker
                    key={`prod-${m.control_number}`}
                    center={[m.production_lat as number, m.production_lon as number]}
                    radius={6}
                    pathOptions={{
                      color: "#8b5cf6",
                      fillColor: "#8b5cf6",
                      fillOpacity: 0.75,
                      weight: 1.5,
                    }}
                  >
                    <Popup>
                      <div className="text-xs space-y-0.5">
                        <div className="font-semibold">{m.label}</div>
                        <div>Production: {m.production_place}</div>
                        {m.production_year != null && (
                          <div className="text-muted">Year: {m.production_year}</div>
                        )}
                        {(m.event_places ?? []).length > 0 && (
                          <div className="text-amber-600">
                            Via: {(m.event_places ?? []).map((ep) => ep.place).join(" → ")}
                          </div>
                        )}
                        <div className="text-emerald-700 font-medium">
                          Holder: {m.holder_label}
                        </div>
                      </div>
                    </Popup>
                  </CircleMarker>
                ))}

                {/* Intermediate waypoint markers (751 related places, 541/583 events, 942 former holders) */}
                {waypointClusters.map((wp, idx) => (
                  <CircleMarker
                    key={`wp-${idx}-${wp.label}`}
                    center={[wp.lat, wp.lon]}
                    radius={Math.min(10, 5 + Math.round(Math.sqrt(wp.count)))}
                    pathOptions={{
                      color: "#f59e0b",
                      fillColor: "#f59e0b",
                      fillOpacity: 0.8,
                      weight: 1.5,
                    }}
                  >
                    <Popup>
                      <div className="text-xs space-y-1">
                        <div className="font-semibold">{wp.label}</div>
                        <div className="text-muted">
                          Intermediate place / former holder ({wp.count} manuscript{wp.count === 1 ? "" : "s"})
                        </div>
                        {wp.sampleMs.length > 0 && (
                          <ul className="list-disc list-inside text-[11px] text-muted">
                            {wp.sampleMs.map((title, i) => (
                              <li key={i} className="truncate max-w-[14rem]">{title}</li>
                            ))}
                          </ul>
                        )}
                      </div>
                    </Popup>
                  </CircleMarker>
                ))}

                {/* Attested current holding institutions */}
                {holderClusters.map((h, idx) => (
                  <CircleMarker
                    key={`holder-${idx}-${h.label}`}
                    center={[h.lat, h.lon]}
                    radius={Math.min(13, 6 + Math.round(Math.sqrt(h.count)))}
                    pathOptions={{
                      color: "#22c55e",
                      fillColor: "#22c55e",
                      fillOpacity: 0.85,
                      weight: 2,
                    }}
                  >
                    <Popup>
                      <div className="text-xs space-y-1">
                        <div className="font-semibold">{h.label}</div>
                        <div className="text-muted">
                          Current holder of {h.count} manuscript{h.count === 1 ? "" : "s"}
                        </div>
                        {h.sampleMs.length > 0 && (
                          <ul className="list-disc list-inside text-[11px] text-muted">
                            {h.sampleMs.map((title, i) => (
                              <li key={i} className="truncate max-w-[14rem]">{title}</li>
                            ))}
                          </ul>
                        )}
                      </div>
                    </Popup>
                  </CircleMarker>
                ))}
              </MapContainer>
            </div>

            {/* Stats sidebar */}
            <div className="space-y-3 text-sm">
              <div className="rounded-xl border border-white/10 p-3">
                <div className="kicker mb-2">In view</div>
                <div className="text-2xl font-bold text-ink">{visible.length}</div>
                <div className="text-xs text-muted">manuscripts by {year}</div>
              </div>
              <div className="rounded-xl border border-white/10 p-3 space-y-1">
                <div className="kicker mb-1">On map</div>
                <div className="text-sm font-semibold text-ink">
                  {withPoint.length} <span className="text-xs text-muted font-normal">production coords</span>
                </div>
                <div className="text-sm font-semibold text-amber-400">
                  {waypointClusters.length} <span className="text-xs text-muted font-normal">intermediate places</span>
                </div>
                <div className="text-sm font-semibold text-emerald-400">
                  {holderClusters.length} <span className="text-xs text-muted font-normal">holding institutions</span>
                </div>
                {noPoint.length > 0 && (
                  <div className="text-[11px] text-muted pt-1 border-t border-white/5">
                    {withAnyTrajectory.length} with trajectory · {noPoint.length} without production place
                  </div>
                )}
              </div>
              <div className="rounded-xl border border-white/10 p-3 space-y-1.5">
                <div className="kicker mb-1">Legend</div>
                <div className="flex items-center gap-2 text-xs">
                  <span className="inline-block w-2.5 h-2.5 rounded-full shrink-0" style={{background: "#8b5cf6"}} />
                  <span>Production place</span>
                </div>
                <div className="flex items-center gap-2 text-xs">
                  <span className="inline-block w-2.5 h-2.5 rounded-full shrink-0" style={{background: "#f59e0b"}} />
                  <span>Intermediate / former place</span>
                </div>
                <div className="flex items-center gap-2 text-xs">
                  <span className="inline-block w-2.5 h-2.5 rounded-full shrink-0" style={{background: "#22c55e"}} />
                  <span>Current holding institution</span>
                </div>
                <div className="text-muted text-[10px] pt-1 border-t border-white/5">
                  – – dashed arcs: production → intermediate places → current holder
                </div>
              </div>
              {holderClusters.length > 0 && (
                <div className="rounded-xl border border-white/10 p-3">
                  <div className="kicker mb-2">Top current holders</div>
                  <ul className="space-y-1 text-xs max-h-36 overflow-y-auto">
                    {holderClusters.slice(0, 6).map((h, i) => (
                      <li key={i} className="flex items-center justify-between gap-2">
                        <span className="truncate text-ink" title={h.label}>{h.label}</span>
                        <span className="text-emerald-400 tabular-nums shrink-0 font-medium">{h.count}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>

          {/* Year slider */}
          <CorpusYearSlider
            yearCounts={data.year_counts}
            year={year}
            onYearChange={(y) => { setPlaying(false); setYear(y); }}
            playing={playing}
            onPlayPause={() => {
              if (playing) { setPlaying(false); return; }
              if (facets && year >= (facets.year_max ?? 1900))
                setYear(facets.year_min ?? 1200);
              setPlaying(true);
            }}
          />
        </>
      )}

      {!loading && !data && (
        <div className="muted text-sm py-10 text-center space-y-2">
          <p>{error ?? "No data yet — build the RDF for at least one run first."}</p>
          {error ? (
            <button
              type="button"
              onClick={reload}
              className="rounded-lg border border-biu-sky/40 bg-biu-sky/15 px-3 py-1 text-xs font-medium text-biu-sky hover:bg-biu-sky/25"
            >
              Retry loading map
            </button>
          ) : null}
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// Root component
// ═══════════════════════════════════════════════════════════════════════════

export default function ProvenanceMapPanel({
  projectId,
  initialCn,
  initialMode,
}: {
  projectId: string;
  initialCn?: string;
  initialMode?: Mode;
}) {
  const [mode, setMode] = useState<Mode>(initialMode ?? "single");
  const [list, setList] = useState<ManuscriptPick[]>([]);

  useEffect(() => {
    if (initialCn) {
      setMode("single");
    } else if (initialMode) {
      setMode(initialMode);
    }
  }, [initialCn, initialMode]);

  useEffect(() => {
    if (mode !== "single") return;
    let cancelled = false;
    researchApi.manuscripts(projectId).then((rows) => {
      if (!cancelled) setList(rows);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [projectId, mode]);

  return (
    <div className="space-y-4">
      <ModeToggle mode={mode} onChange={setMode} />
      {mode === "single" ? (
        <SingleManuscriptView projectId={projectId} list={list} initialCn={initialCn} />
      ) : (
        <CorpusView projectId={projectId} />
      )}
    </div>
  );
}

function Legend() {
  return (
    <div className="rounded-xl border border-white/10 p-3 text-xs space-y-1.5">
      <div className="kicker mb-1">Legend</div>
      {(["production", "acquisition", "conservation", "exhibition", "significant_place", "owner", "current_holder"] as const).map((k) => (
        <div key={k} className="flex items-center gap-2">
          <span
            className="inline-block w-2.5 h-2.5 rounded-full"
            style={{background: KIND_COLOR[k]}}
          />
          <span className="text-ink">{KIND_LABEL[k]}</span>
        </div>
      ))}
      <div className="pt-1.5 border-t border-white/10 mt-1.5 text-muted">
        <div>— solid arc: certain anchors</div>
        <div>– – dashed arc / faded dot: inferred order or location</div>
      </div>
    </div>
  );
}
