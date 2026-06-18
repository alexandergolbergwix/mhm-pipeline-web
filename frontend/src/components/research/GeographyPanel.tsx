/**
 * Geography Panel — manuscript production and mentioned places.
 *
 * Two views toggled by the user:
 *   "Table"   — sortable, filterable table of place rows (default).
 *   "Heatmap" — SVG dot map sized by manuscript count, positioned by lat/lon.
 *
 * Both views use data from the server:
 *   - Table  → GET /geography               → PlaceRow[]
 *   - Heatmap → GET /geography?mode=heatmap  → HeatmapPoint[]
 *
 * No leaflet dependency — the heatmap is a pure SVG Mercator projection so it
 * renders correctly even when npm packages cannot be installed offline.
 */
import {useMemo, useState} from "react";
import {researchApi, type HeatmapPoint, type PlaceRow} from "@/api/research";
import {PanelShell, useAsync} from "./_shared";
import {Glass} from "@/components/glass";

// ── colour helpers ─────────────────────────────────────────────────────────

const TYPE_COLOR: Record<string, string> = {
  production: "#38bdf8",
  mentioned:  "#fb923c",
  both:       "#a78bfa",
};

// ── mini OSM iframe for table drill-down ──────────────────────────────────

function MiniMap({lat, lon, label}: {lat: number; lon: number; label: string}) {
  const src = `https://www.openstreetmap.org/export/embed.html?bbox=${lon - 1},${lat - 1},${lon + 1},${lat + 1}&layer=mapnik&marker=${lat},${lon}`;
  return (
    <div className="rounded-xl overflow-hidden border border-white/10 mt-3">
      <p className="text-xs muted px-3 py-1.5 border-b border-white/10 truncate">{label}</p>
      <iframe
        title={label}
        src={src}
        width="100%"
        height="280"
        style={{border: 0, display: "block"}}
        loading="lazy"
        referrerPolicy="no-referrer"
      />
    </div>
  );
}

// ── SVG heatmap ────────────────────────────────────────────────────────────

/** Simple Web Mercator projection clamped to [−85, 85] lat, [−180, 180] lon. */
function project(lat: number, lon: number, w: number, h: number): [number, number] {
  const x = ((lon + 180) / 360) * w;
  const latRad = (lat * Math.PI) / 180;
  const mercN = Math.log(Math.tan(Math.PI / 4 + latRad / 2));
  const y = (0.5 - mercN / (2 * Math.PI)) * h;
  return [x, y];
}

function SvgHeatmap({
  points,
  typeFilter,
}: {
  points: HeatmapPoint[];
  typeFilter: "all" | "production" | "mentioned" | "both";
}) {
  const W = 720;
  const H = 380;

  const visible = useMemo(() => {
    return points.filter(
      (p) => typeFilter === "all" || p.type === typeFilter || p.type === "both",
    );
  }, [points, typeFilter]);

  const maxWeight = useMemo(() => Math.max(1, ...visible.map((p) => p.weight)), [visible]);

  const [hovered, setHovered] = useState<HeatmapPoint | null>(null);

  return (
    <div className="relative rounded-xl overflow-hidden border border-white/10 surface-inset">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        style={{display: "block"}}
        className="select-none"
      >
        <rect width={W} height={H} fill="var(--map-ocean)" />

        {/* equator and prime meridian guides */}
        <line
          x1={W / 2} y1={0} x2={W / 2} y2={H}
          stroke="white" strokeOpacity={0.04} strokeWidth={0.5}
        />
        <line
          x1={0} y1={H / 2} x2={W} y2={H / 2}
          stroke="white" strokeOpacity={0.04} strokeWidth={0.5}
        />

        {visible.map((p, i) => {
          const [cx, cy] = project(p.lat, p.lon, W, H);
          const r = 4 + 10 * Math.sqrt(p.weight / maxWeight);
          const color = TYPE_COLOR[p.type] ?? "#94a3b8";
          return (
            <g key={i}>
              {/* glow */}
              <circle
                cx={cx} cy={cy}
                r={r + 4}
                fill={color}
                fillOpacity={0.08}
              />
              {/* dot */}
              <circle
                cx={cx} cy={cy}
                r={r}
                fill={color}
                fillOpacity={hovered?.place === p.place ? 0.9 : 0.55}
                stroke={color}
                strokeOpacity={0.8}
                strokeWidth={0.8}
                style={{cursor: "pointer"}}
                onMouseEnter={() => setHovered(p)}
                onMouseLeave={() => setHovered(null)}
              />
            </g>
          );
        })}
      </svg>

      {/* tooltip */}
      {hovered && (
        <Glass className="absolute bottom-3 left-3 rounded-lg px-3 py-2 text-xs pointer-events-none">
          <p className="font-medium text-ink">{hovered.place_label}</p>
          <p className="muted">
            {hovered.weight} ms · {hovered.type}
          </p>
        </Glass>
      )}

      {/* legend */}
      <Glass className="absolute top-3 right-3 rounded-lg px-3 py-2 text-xs flex flex-col gap-1.5">
        {(["production", "mentioned", "both"] as const).map((t) => (
          <span key={t} className="flex items-center gap-1.5">
            <span
              className="inline-block w-2.5 h-2.5 rounded-full"
              style={{background: TYPE_COLOR[t]}}
            />
            <span className="muted">{t}</span>
          </span>
        ))}
      </Glass>
    </div>
  );
}

// ── table view ─────────────────────────────────────────────────────────────

function TableView({projectId}: {projectId: string}) {
  const {data, loading, error} = useAsync<PlaceRow[]>(
    () => researchApi.geography(projectId),
    [projectId],
  );

  const [typeFilter, setTypeFilter] = useState<"all" | "production" | "mentioned">("all");
  const [search, setSearch]         = useState("");
  const [selected, setSelected]     = useState<PlaceRow | null>(null);

  const places = useMemo(() => {
    if (!data) return [];
    return data.filter(
      (p) =>
        (typeFilter === "all" || p.type === typeFilter) &&
        (search === "" || p.place_label.toLowerCase().includes(search.toLowerCase())),
    );
  }, [data, typeFilter, search]);

  const withCoords    = places.filter((p) => p.lat != null);
  const withoutCoords = places.filter((p) => p.lat == null);

  if (loading) return <p className="text-sm muted">Loading places…</p>;
  if (error)   return <p className="text-sm text-danger">{error}</p>;
  if (!data?.length) return (
    <p className="text-sm muted text-center py-8">
      No production sites or mentioned places found in this run&rsquo;s graph.
    </p>
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3 items-center">
        <div className="flex gap-1">
          {(["all", "production", "mentioned"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTypeFilter(t)}
              className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
                typeFilter === t
                  ? "bg-biu-sky/20 border-biu-sky text-biu-sky"
                  : "border-white/10 text-muted hover:border-white/30"
              }`}
            >
              {t === "all" ? "All" : t.charAt(0).toUpperCase() + t.slice(1)}
              {t !== "all" && (
                <span
                  className="ml-1.5 inline-block w-2 h-2 rounded-full"
                  style={{background: TYPE_COLOR[t]}}
                />
              )}
            </button>
          ))}
        </div>
        <input
          type="search"
          placeholder="Filter places…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="input-glass text-sm flex-1 min-w-[160px]"
        />
      </div>

      <div className="flex gap-4">
        <div className="flex-1 overflow-auto max-h-[420px]">
          <table className="w-full text-sm">
            <thead className="sticky top-0 table-head">
              <tr className="text-left text-xs muted border-b border-white/10">
                <th className="py-2 pr-4 font-medium">Place</th>
                <th className="py-2 pr-4 font-medium">Type</th>
                <th className="py-2 pr-2 font-medium text-right">Mss</th>
                <th className="py-2 font-medium text-right">Coords</th>
              </tr>
            </thead>
            <tbody>
              {places.map((p) => (
                <tr
                  key={p.place}
                  onClick={() => setSelected(selected?.place === p.place ? null : p)}
                  className={`border-b border-white/5 cursor-pointer transition-colors ${
                    selected?.place === p.place
                      ? "bg-biu-sky/10"
                      : "hover:bg-white/5"
                  }`}
                >
                  <td className="py-1.5 pr-4 text-ink font-medium">{p.place_label}</td>
                  <td className="py-1.5 pr-4">
                    <span
                      className="text-xs px-2 py-0.5 rounded-full"
                      style={{
                        background: `${TYPE_COLOR[p.type] ?? "#94a3b8"}22`,
                        color:       TYPE_COLOR[p.type] ?? "#94a3b8",
                      }}
                    >
                      {p.type}
                    </span>
                  </td>
                  <td className="py-1.5 pr-2 text-right tabular-nums text-biu-sky">
                    {p.ms_count}
                  </td>
                  <td className="py-1.5 text-right">
                    {p.lat != null
                      ? <span className="text-green-400 text-xs">✓</span>
                      : <span className="muted text-xs">—</span>
                    }
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {selected && selected.lat != null && selected.lon != null && (
          <div className="w-72 flex-shrink-0">
            <p className="text-xs muted mb-1">
              {selected.ms_labels.length} manuscript{selected.ms_labels.length !== 1 ? "s" : ""}:
            </p>
            <ul className="text-xs muted list-disc list-inside space-y-0.5 max-h-16 overflow-y-auto mb-1">
              {selected.ms_labels.map((m, i) => <li key={i} className="truncate">{m}</li>)}
            </ul>
            <MiniMap lat={selected.lat} lon={selected.lon} label={selected.place_label} />
          </div>
        )}
      </div>

      {withCoords.length > 0 && withoutCoords.length > 0 && (
        <p className="text-xs muted">
          {withCoords.length} place{withCoords.length !== 1 ? "s" : ""} with coordinates ·{" "}
          {withoutCoords.length} without
        </p>
      )}
    </div>
  );
}

// ── heatmap view ───────────────────────────────────────────────────────────

function HeatmapView({projectId}: {projectId: string}) {
  const {data, loading, error} = useAsync<HeatmapPoint[]>(
    () => researchApi.geographyHeatmap(projectId),
    [projectId],
  );

  const [typeFilter, setTypeFilter] = useState<"all" | "production" | "mentioned" | "both">("all");

  if (loading) return <p className="text-sm muted">Loading heatmap…</p>;
  if (error)   return <p className="text-sm text-danger">{error}</p>;
  if (!data?.length) return (
    <p className="text-sm muted text-center py-8">
      No places have map coordinates in this run&rsquo;s graph. Coordinates come
      from geo-enriched subject places (GeoNames/Wikidata); production and
      mentioned places carry names only, so they appear in the Table view but
      not on the map.
    </p>
  );

  return (
    <div className="space-y-4">
      <div className="flex gap-1">
        {(["all", "production", "mentioned", "both"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTypeFilter(t)}
            className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
              typeFilter === t
                ? "bg-biu-sky/20 border-biu-sky text-biu-sky"
                : "border-white/10 text-muted hover:border-white/30"
            }`}
          >
            {t === "all" ? "All types" : t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>
      <SvgHeatmap points={data} typeFilter={typeFilter} />
      <p className="text-xs muted">
        {data.length} place{data.length !== 1 ? "s" : ""} with coordinates ·
        dot size ∝ manuscript count
      </p>
    </div>
  );
}

// ── panel ─────────────────────────────────────────────────────────────────

export default function GeographyPanel({projectId}: {projectId: string}) {
  const [view, setView] = useState<"table" | "heatmap">("table");

  return (
    <PanelShell
      title="Geographic Distribution"
      subtitle="Production sites and mentioned places across the manuscript corpus"
    >
      {/* view toggle */}
      <div className="flex gap-2 items-center mb-2">
        {(["table", "heatmap"] as const).map((v) => (
          <button
            key={v}
            onClick={() => setView(v)}
            className={`text-xs px-3 py-1.5 rounded-full border font-medium transition-colors ${
              view === v
                ? "bg-biu-sky/20 border-biu-sky text-biu-sky"
                : "border-white/10 text-muted hover:border-white/30"
            }`}
          >
            {v === "table" ? "Table" : "Heatmap"}
          </button>
        ))}
      </div>

      {view === "table"   && <TableView   projectId={projectId} />}
      {view === "heatmap" && <HeatmapView projectId={projectId} />}
    </PanelShell>
  );
}
