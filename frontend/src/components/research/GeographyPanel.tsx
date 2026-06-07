/**
 * Geography Panel — shows manuscript production and mentioned places.
 *
 * The server returns pre-grouped place rows {place, place_label, lat, lon,
 * type, ms_count, ms_labels} so there is no client-side aggregation.
 * Client-side type/text search is O(places) and stays on the browser.
 *
 * Uses an <iframe> embedding an OpenStreetMap search for single-place
 * drill-down, and a sortable summary table for all places.  This
 * approach requires no additional npm packages (leaflet is not
 * installed in this project).
 */
import {useMemo, useState} from "react";
import {researchApi, type PlaceRow} from "@/api/research";
import {PanelShell, useAsync} from "./_shared";

const TYPE_COLOR: Record<string, string> = {
  production: "#38bdf8",
  mentioned:  "#fb923c",
};

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

export default function GeographyPanel({projectId}: {projectId: string}) {
  const {data, loading, error} = useAsync<PlaceRow[]>(
    () => researchApi.geography(projectId),
    [projectId],
  );

  const [typeFilter, setTypeFilter] = useState<"all" | "production" | "mentioned">("all");
  const [search, setSearch]         = useState("");
  const [selected, setSelected]     = useState<PlaceRow | null>(null);

  // Client-side filter — O(places), fine since grouping already done server-side
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

  return (
    <PanelShell
      title="Geographic Distribution"
      subtitle="Production sites and mentioned places across the manuscript corpus"
      loading={loading}
      empty={!loading && !data?.length}
    >
      {error && <p className="text-red-400 text-sm">{error}</p>}
      {data && data.length > 0 && (
        <div className="space-y-4">
          {/* Controls */}
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
            {/* Table */}
            <div className="flex-1 overflow-auto max-h-[420px]">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-slate-900/90 backdrop-blur-sm">
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
                          : <span className="text-slate-600 text-xs">—</span>
                        }
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Mini-map drill-down */}
            {selected && selected.lat != null && selected.lon != null && (
              <div className="w-72 flex-shrink-0">
                <p className="text-xs muted mb-1">
                  {selected.ms_labels.length} manuscript{selected.ms_labels.length !== 1 ? "s" : ""}:
                </p>
                <ul className="text-xs muted list-disc list-inside space-y-0.5 max-h-16 overflow-y-auto mb-1">
                  {selected.ms_labels.map((m, i) => <li key={i} className="truncate">{m}</li>)}
                </ul>
                <MiniMap
                  lat={selected.lat}
                  lon={selected.lon}
                  label={selected.place_label}
                />
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
      )}
    </PanelShell>
  );
}
