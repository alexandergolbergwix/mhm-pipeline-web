/**
 * Points map for agent-placed artifacts (`kind: "map"`, `content.map === "points"`).
 *
 * One dot per place; each popup lists the manuscripts that mention the
 * place with links to their Wikidata entities.
 */
import {useEffect, useMemo} from "react";
import {CircleMarker, MapContainer, Popup, TileLayer, useMap} from "react-leaflet";
import "leaflet/dist/leaflet.css";

export type WikidataPlacePoint = {
  place_qid: string;
  place_label: string;
  lat: number;
  lon: number;
  manuscripts: Array<{
    item_qid: string;
    item_label: string;
    property: string;
    property_label?: string;
  }>;
};

const WD_URL = (qid: string) => `https://www.wikidata.org/wiki/${qid}`;

function FitPoints({points}: {points: WikidataPlacePoint[]}) {
  const map = useMap();
  useEffect(() => {
    const pts = points.map((p) => [p.lat, p.lon] as [number, number]);
    if (pts.length === 1) map.setView(pts[0], 5);
    else if (pts.length > 1) map.fitBounds(pts, {padding: [40, 40]});
  }, [points, map]);
  return null;
}

export default function WikidataPlacesPanel({points}: {points: WikidataPlacePoint[]}) {
  const totalMentions = useMemo(
    () => points.reduce((sum, p) => sum + (p.manuscripts?.length || 0), 0),
    [points],
  );

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div
        className="lg:col-span-2 rounded-xl overflow-hidden border border-white/10"
        data-testid="wikidata-places-map"
        style={{height: 480}}
      >
        <MapContainer center={[31.5, 35]} zoom={4} style={{height: "100%", width: "100%"}} scrollWheelZoom>
          <TileLayer
            attribution="&copy; OpenStreetMap contributors"
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <FitPoints points={points} />
          {points.map((p) => (
            <CircleMarker
              key={p.place_qid}
              center={[p.lat, p.lon]}
              radius={Math.min(12, 6 + (p.manuscripts?.length || 0))}
              pathOptions={{color: "#8b5cf6", fillColor: "#8b5cf6", fillOpacity: 0.75, weight: 1.5}}
            >
              <Popup>
                <div className="text-xs space-y-1 min-w-[16rem]">
                  <div className="font-semibold text-sm">
                    <a href={WD_URL(p.place_qid)} target="_blank" rel="noreferrer">
                      {p.place_label}
                    </a>
                  </div>
                  <div className="text-muted">{p.manuscripts?.length || 0} manuscript(s) mention this place:</div>
                  <ul className="space-y-0.5">
                    {(p.manuscripts || []).slice(0, 8).map((m) => (
                      <li key={`${p.place_qid}-${m.item_qid}-${m.property}`}>
                        <a href={WD_URL(m.item_qid)} target="_blank" rel="noreferrer" className="font-medium">
                          {m.item_label} ({m.item_qid})
                        </a>
                        {m.property_label ? <span className="text-muted"> · {m.property_label}</span> : null}
                      </li>
                    ))}
                  </ul>
                  {(p.manuscripts?.length || 0) > 8 ? (
                    <div className="text-muted">…and {(p.manuscripts?.length || 0) - 8} more</div>
                  ) : null}
                </div>
              </Popup>
            </CircleMarker>
          ))}
        </MapContainer>
      </div>
      <div className="rounded-xl border border-white/10 p-3 overflow-auto max-h-[480px]">
        <div className="kicker mb-2">
          Places ({points.length}) · {totalMentions} mentions
        </div>
        <ul className="space-y-1 text-xs">
          {points.map((p) => (
            <li key={p.place_qid} className="flex items-center gap-2">
              <span className="inline-block h-2.5 w-2.5 rounded-full shrink-0" style={{background: "#8b5cf6"}} />
              <a href={WD_URL(p.place_qid)} target="_blank" rel="noreferrer" className="text-ink hover:underline truncate">
                {p.place_label}
              </a>
              <span className="text-muted ml-auto shrink-0 tabular-nums">{p.manuscripts?.length || 0}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
