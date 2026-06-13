/**
 * Provenance Movement Map — Phase 1 (single manuscript).
 *
 * Pick a manuscript and see its provenance laid out on an interactive map:
 * production place, significant places, geolocated owners, and the current
 * holder, connected by arcs in inferred chronological order, with a year
 * slider that reveals the movement over time.
 *
 * Integrity: owner points are biographical proxies (flagged inferred, dashed
 * arcs, "inferred" badge + the Wikidata property used). Owners that are
 * unapproved, low-confidence, anachronistic, or un-geolocatable never get a
 * map point — they appear in the "not mapped" side list with the reason.
 */
import {useEffect, useMemo, useRef, useState} from "react";
import {MapContainer, TileLayer, CircleMarker, Polyline, Popup, useMap} from "react-leaflet";
import "leaflet/dist/leaflet.css";
import {researchApi, type ManuscriptPick, type MapStop, type ProvenanceMap} from "@/api/research";
import {mappableStops, quadraticArcPoints, stopRevealedAt, timeBounds} from "./provenanceArc";

const KIND_COLOR: Record<string, string> = {
  production:        "#8b5cf6", // violet
  owner:             "#38bdf8", // sky
  significant_place: "#f59e0b", // amber
  current_holder:    "#22c55e", // green
};

const KIND_LABEL: Record<string, string> = {
  production:        "Production",
  owner:             "Owner (inferred location)",
  significant_place: "Significant place",
  current_holder:    "Current holder",
};

function FitBounds({stops}: {stops: MapStop[]}) {
  const map = useMap();
  useEffect(() => {
    const pts = mappableStops(stops).map((s) => [s.lat as number, s.lon as number] as [number, number]);
    if (pts.length === 1) {
      map.setView(pts[0], 5);
    } else if (pts.length > 1) {
      map.fitBounds(pts, {padding: [40, 40]});
    }
  }, [stops, map]);
  return null;
}

function yearText(s: MapStop): string {
  if (s.is_present) return "present";
  if (s.year != null) return String(s.year);
  if (s.year_earliest != null && s.year_latest != null) {
    return s.year_earliest === s.year_latest
      ? String(s.year_earliest)
      : `${s.year_earliest}–${s.year_latest}`;
  }
  if (s.birth_year != null) return `b. ${s.birth_year}`;
  return "undated";
}

export default function ProvenanceMapPanel({projectId}: {projectId: string}) {
  const [list, setList] = useState<ManuscriptPick[]>([]);
  const [filter, setFilter] = useState("");
  const [cn, setCn] = useState<string>("");
  const [data, setData] = useState<ProvenanceMap | null>(null);
  const [loading, setLoading] = useState(false);
  const [includeUnapproved, setIncludeUnapproved] = useState(false);
  const [year, setYear] = useState<number>(0);
  const [playing, setPlaying] = useState(false);
  const playRef = useRef<number | null>(null);

  // Load the manuscript picker list.
  useEffect(() => {
    let cancelled = false;
    researchApi.manuscripts(projectId).then((rows) => {
      if (!cancelled) setList(rows);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [projectId]);

  // Load the selected manuscript's map.
  useEffect(() => {
    if (!cn) { setData(null); return; }
    let cancelled = false;
    setLoading(true);
    researchApi.provenanceMap(projectId, cn, includeUnapproved)
      .then((m) => { if (!cancelled) setData(m); })
      .catch(() => { if (!cancelled) setData(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [projectId, cn, includeUnapproved]);

  const bounds = useMemo(() => (data ? timeBounds(data.stops) : null), [data]);
  const maxTime = bounds?.max ?? 0;

  // Reset slider to max (show everything) whenever new data loads.
  useEffect(() => {
    if (bounds) setYear(bounds.max);
  }, [bounds]);

  // Play animation: advance year from min → max, then stop.
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
          points: quadraticArcPoints([a.lat, a.lon as number], [b.lat, b.lon as number]),
          inferred: e.inferred,
        };
      })
      .filter((x): x is {points: [number, number][]; inferred: boolean} => x !== null);
  }, [data, revealed, stops]);

  return (
    <div className="space-y-4">
      {/* Picker */}
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
              {(m.label ?? m.control_number)}{m.production_year != null ? ` · ${m.production_year}` : ""}
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

      {loading && <div className="muted text-sm py-6 text-center">Loading map…</div>}

      {data && !loading && (
        <>
          {/* Map + side panel */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <div className="lg:col-span-2 rounded-xl overflow-hidden border border-white/10" data-testid="prov-map" style={{height: 460}}>
              <MapContainer center={[31.5, 35]} zoom={3} style={{height: "100%", width: "100%"}} scrollWheelZoom>
                <TileLayer
                  attribution='&copy; OpenStreetMap contributors'
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
                      radius={s.kind === "production" || s.kind === "current_holder" ? 9 : 7}
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
                          <div>{KIND_LABEL[s.kind] ?? s.kind} · {yearText(s)}</div>
                          {s.inferred_geo && (
                            <div className="text-amber-600">
                              inferred location{s.geo_source_label ? ` (via ${s.geo_source_label})` : ""}
                            </div>
                          )}
                        </div>
                      </Popup>
                    </CircleMarker>
                  ) : null,
                )}
              </MapContainer>
            </div>

            {/* Timeline + legend side panel */}
            <div className="space-y-3">
              <Legend />
              <div className="rounded-xl border border-white/10 p-3">
                <div className="kicker mb-2">Sequence</div>
                <ol className="space-y-1.5">
                  {stops.map((s, i) => (
                    <li
                      key={`t-${i}`}
                      className={`flex items-center gap-2 text-sm transition-opacity ${revealed[i] ? "opacity-100" : "opacity-30"}`}
                    >
                      <span
                        className="inline-block w-2.5 h-2.5 rounded-full shrink-0"
                        style={{background: KIND_COLOR[s.kind] ?? "#cbd5e1"}}
                      />
                      <span className="text-ink truncate">{s.label}</span>
                      <span className="text-muted text-xs ml-auto shrink-0">{yearText(s)}</span>
                      {s.inferred_geo && (
                        <span className="text-[10px] px-1 rounded bg-amber-500/20 text-amber-400 shrink-0">inferred</span>
                      )}
                    </li>
                  ))}
                </ol>
              </div>

              {data.dropped.length > 0 && (
                <div className="rounded-xl border border-white/10 p-3">
                  <div className="kicker mb-2">Not mapped ({data.dropped.length})</div>
                  <ul className="space-y-1 text-xs">
                    {data.dropped.map((d, i) => (
                      <li key={`d-${i}`} className="flex justify-between gap-2">
                        <span className="text-ink truncate">{d.label}</span>
                        <span className="text-muted shrink-0">{d.reason.replace(/_/g, " ")}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>

          {/* Year slider */}
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
              <span className="text-xs text-muted tabular-nums w-12 text-right">{bounds.min}</span>
              <input
                type="range"
                min={bounds.min}
                max={bounds.max}
                value={year}
                onChange={(e) => { setPlaying(false); setYear(Number(e.target.value)); }}
                aria-label="Year"
                className="flex-1"
              />
              <span className="text-xs text-muted tabular-nums w-12">{bounds.max}</span>
              <span className="text-sm text-ink tabular-nums w-14 text-right">{year}</span>
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

function Legend() {
  return (
    <div className="rounded-xl border border-white/10 p-3 text-xs space-y-1.5">
      <div className="kicker mb-1">Legend</div>
      {(["production", "significant_place", "owner", "current_holder"] as const).map((k) => (
        <div key={k} className="flex items-center gap-2">
          <span className="inline-block w-2.5 h-2.5 rounded-full" style={{background: KIND_COLOR[k]}} />
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
