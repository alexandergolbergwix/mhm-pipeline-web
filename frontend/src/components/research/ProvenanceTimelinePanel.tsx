/**
 * ProvenanceTimelinePanel — horizontal timeline of provenance events for a
 * single manuscript: production event → ownership events.
 *
 * Used as a 7th tab in LinkedDataExplorer ("Provenance") and embedded on the
 * EntityPage for manuscript entities.
 *
 * The user picks a manuscript from a searchable list, then the panel shows
 * a scrollable horizontal timeline with:
 *   - A "Production" chip with date (certain or range) and place.
 *   - One "Owner" chip per hm:has_owner person, with optional lifespan
 *     band when ?overlay=lifespans is toggled on.
 *
 * No D3 dependency — the timeline is a pure CSS/Tailwind flex row so it
 * renders consistently across browsers without SVG concerns.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import {
  researchApi,
  type ProvenanceEvent,
  type ProvenanceTimeline,
} from "@/api/research";
import { PanelShell, useAsync } from "./_shared";

// ── single event chip ──────────────────────────────────────────────────────

function yearRange(ev: ProvenanceEvent): string {
  if (ev.year != null) return String(ev.year);
  if (ev.year_earliest != null || ev.year_latest != null) {
    const from = ev.year_earliest ?? "?";
    const to   = ev.year_latest   ?? "?";
    return `${from}–${to}`;
  }
  return "";
}

function EventChip({
  ev,
  projectId,
  showLifespan,
}: {
  ev: ProvenanceEvent;
  projectId: string;
  showLifespan: boolean;
}) {
  const isProduction = ev.type === "production";
  const bgCls = isProduction
    ? "surface-inset border border-[var(--line)]"
    : "bg-sky-900/50 border-sky-500/50";

  return (
    <div
      className={`shrink-0 rounded-xl border px-4 py-3 text-sm min-w-[9rem] max-w-[14rem] ${bgCls}`}
    >
      <p className="text-[10px] uppercase tracking-widest font-semibold text-disabled mb-1">
        {isProduction ? "Production" : "Owner"}
      </p>

      {/* label / name */}
      {ev.uri && !isProduction ? (
        <Link
          to={`/projects/${projectId}/entity?uri=${encodeURIComponent(ev.uri)}`}
          className="block text-biu-sky hover:underline font-medium leading-snug truncate"
        >
          {ev.label}
        </Link>
      ) : (
        <p className="font-medium text-subtle leading-snug truncate">{ev.label}</p>
      )}

      {/* year */}
      {yearRange(ev) && (
        <p className="text-xs text-faint mt-0.5">{yearRange(ev)}</p>
      )}

      {/* place (production) */}
      {isProduction && ev.place && (
        <p className="text-xs text-faint truncate mt-0.5">{ev.place}</p>
      )}

      {/* lifespan (owners) */}
      {showLifespan && !isProduction && (ev.owner_birth || ev.owner_death) && (
        <p className="text-[11px] text-disabled mt-1">
          {ev.owner_birth && <>b.{ev.owner_birth} </>}
          {ev.owner_death && <>d.{ev.owner_death}</>}
        </p>
      )}
    </div>
  );
}

// ── timeline ──────────────────────────────────────────────────────────────

function Timeline({
  data,
  projectId,
  showLifespan,
}: {
  data: ProvenanceTimeline;
  projectId: string;
  showLifespan: boolean;
}) {
  if (data.events.length === 0) {
    return (
      <p className="text-sm text-disabled italic">
        No provenance data available for this manuscript.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto pb-2">
      <div className="flex items-start gap-3 min-w-max">
        {data.events.map((ev, i) => (
          <div key={i} className="flex items-center gap-3">
            <EventChip ev={ev} projectId={projectId} showLifespan={showLifespan} />
            {i < data.events.length - 1 && (
              <span className="text-disabled text-lg">→</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── manuscript picker (simple searchable list) ────────────────────────────

function MsPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (uri: string) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <label className="text-xs text-faint shrink-0">Manuscript URI:</label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="urn:hm:MS_…"
        className="input-glass flex-1 !py-1.5 text-sm"
      />
    </div>
  );
}

// ── panel ─────────────────────────────────────────────────────────────────

export default function ProvenanceTimelinePanel({
  projectId,
}: {
  projectId: string;
}) {
  const [msUri, setMsUri] = useState("");
  const [showLifespan, setShowLifespan] = useState(false);

  const fetchKey = msUri ? `${msUri}:${showLifespan}` : null;
  const { data, loading, error } = useAsync<ProvenanceTimeline>(
    () =>
      researchApi.provenanceTimeline(
        projectId,
        msUri,
        showLifespan ? "lifespans" : undefined,
      ),
    [fetchKey],
    !fetchKey,
  );

  return (
    <PanelShell
      title="Provenance Timeline"
      subtitle="Production event and ownership chain for a manuscript"
    >
      <div className="space-y-4">
        {/* controls */}
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex-1">
            <MsPicker value={msUri} onChange={setMsUri} />
          </div>
          <label className="flex items-center gap-2 text-xs text-faint cursor-pointer shrink-0">
            <input
              type="checkbox"
              checked={showLifespan}
              onChange={(e) => setShowLifespan(e.target.checked)}
              className="accent-biu-sky"
            />
            Show owner lifespans
          </label>
        </div>

        {/* content */}
        {!msUri && (
          <p className="text-sm text-disabled italic">
            Enter a manuscript URI to see its provenance chain.
          </p>
        )}

        {loading && (
          <p className="text-sm text-disabled">Loading provenance…</p>
        )}

        {error && (
          <p className="text-sm text-danger">{error}</p>
        )}

        {data && (
          <div className="space-y-2">
            <p className="text-sm font-medium text-subtle">
              {data.ms_label ?? data.ms}
            </p>
            <Timeline
              data={data}
              projectId={projectId}
              showLifespan={showLifespan}
            />
          </div>
        )}
      </div>
    </PanelShell>
  );
}
