/**
 * Points map for agent-placed artifacts (`kind: "map"`, `content.map === "points"`).
 *
 * Each point is a place-valued claim on one of the project's uploaded
 * Wikidata items; popups link to the item and the place on Wikidata.
 */
import {useEffect, useMemo} from "react";
import {CircleMarker, MapContainer, Popup, TileLayer, useMap} from "react-leaflet";
import "leaflet/dist/leaflet.css";

export type WikidataPlacePoint = {
  item_qid: string;
  item_label: string;
  property: string;
  property_label?: string;
  place_qid: string;
  place_label: string;
  lat: number;
  lon: number;
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
  const groups = useMemo(() => {
    const byPlace = new Map<string, WikidataPlacePoint[]>();
    for (const p of points) {
      const list = byPlace.get(p.place_qid) ?? [];
      list.push(p);
      byPlace.set(p.place_qid, list);
    }
    return [...byPlace.values()].sort((a, b) => b.length - a.length);
  }, [points]);

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
          {points.map((p, i) => (
            <CircleMarker
              key={`${p.item_qid}-${p.place_qid}-${i}`}
              center={[p.lat, p.lon]}
              radius={7}
              pathOptions={{color: "#8b5cf6", fillColor: "#8b5cf6", fillOpacity: 0.75, weight: 1.5}}
            >
              <Popup>
                <div className="text-xs space-y-1">
                  <div className="font-semibold">
                    <a href={WD_URL(p.item_qid)} target="_blank" rel="noreferrer">
                      {p.item_label} ({p.item_qid})
                    </a>
                  </div>
                  <div>
                    {p.property_label || p.property}
                    {" → "}
                    <a href={WD_URL(p.place_qid)} target="_blank" rel="noreferrer">
                      {p.place_label} ({p.place_qid})
                    </a>
                  </div>
                </div>
              </Popup>
            </CircleMarker>
          ))}
        </MapContainer>
      </div>
      <div className="rounded-xl border border-white/10 p-3 overflow-auto max-h-[480px]">
        <div className="kicker mb-2">Places ({groups.length})</div>
        <ul className="space-y-1 text-xs">
          {groups.map((group) => (
            <li key={group[0].place_qid} className="flex items-center gap-2">
              <span className="inline-block h-2.5 w-2.5 rounded-full shrink-0" style={{background: "#8b5cf6"}} />
              <a href={WD_URL(group[0].place_qid)} target="_blank" rel="noreferrer" className="text-ink hover:underline truncate">
                {group[0].place_label}
              </a>
              <span className="text-muted ml-auto shrink-0 tabular-nums">{group.length}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
