import {useState} from "react";

import type {HmoCoverageEntry} from "@/api/hmoStudio";
import {ClickDetailPopover} from "@/components/ClickDetailPopover";
import {GlassPill} from "@/components/glass";
import {
  coverageRowHoverText,
  projectionStatusExplanation,
  projectionStatusLabel,
  wikidataPropertyHref,
  wikidataPropertyLabel,
} from "@/utils/hmoCoverageExplain";

function statusTone(status: HmoCoverageEntry["projection_status"]): string {
  if (status === "direct_wikidata_item") return "text-biu-sky";
  if (status === "summarized_in_wikidata") return "text-warn";
  if (status === "hmo_or_wikibase_only") return "muted";
  return "text-danger";
}

export function CoverageClassRow({
  row,
  onExplain,
}: {
  row: HmoCoverageEntry;
  onExplain: (row: HmoCoverageEntry, x: number, y: number) => void;
}) {
  const hover = coverageRowHoverText(row);
  const statusLabel = projectionStatusLabel(row.projection_status);
  const tone = statusTone(row.projection_status);

  return (
    <tr
      className="border-t border-white/5 cursor-pointer hover:bg-white/5 transition-colors"
      title={hover}
      data-testid={`hmo-coverage-row-${row.class_local_name}`}
      onClick={(e) => onExplain(row, e.clientX, e.clientY)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
          onExplain(row, rect.left + 24, rect.bottom);
        }
      }}
      tabIndex={0}
      role="button"
      aria-label={`Explain ${row.class_local_name} Wikidata projection`}
    >
      <td className="px-3 py-2">
        <span className="font-mono text-xs">{row.class_local_name}</span>
        {row.class_label && row.class_label !== row.class_local_name && (
          <span className="muted text-xs"> · {row.class_label}</span>
        )}
      </td>
      <td className="px-3 py-2">
        <GlassPill
          className={`px-2 py-0.5 text-[10px] kicker ${tone}`}
          title={projectionStatusExplanation(row.projection_status)}
        >
          {statusLabel}
        </GlassPill>
      </td>
      <td className="px-3 py-2 text-right font-mono text-xs">
        {row.hmo_node_count}
      </td>
      <td className="px-3 py-2 text-right font-mono text-xs">
        {row.projected_item_count}
      </td>
      <td className="px-3 py-2 text-xs muted">
        {row.wikidata_properties.length > 0
          ? row.wikidata_properties.join(" · ")
          : "—"}
      </td>
    </tr>
  );
}

export function CoverageClassDetailPopover({
  row,
  x,
  y,
  onClose,
}: {
  row: HmoCoverageEntry;
  x: number;
  y: number;
  onClose: () => void;
}) {
  const statusLabel = projectionStatusLabel(row.projection_status);
  const tone = statusTone(row.projection_status);

  return (
    <ClickDetailPopover
      x={x}
      y={y}
      title={row.class_local_name}
      testId={`hmo-coverage-detail-${row.class_local_name}`}
      panelClassName="w-[min(440px,calc(100vw-1.5rem))] max-h-[min(480px,75vh)]"
      onClose={onClose}
    >
      {row.class_label && row.class_label !== row.class_local_name && (
        <div className="muted text-[11px]">{row.class_label}</div>
      )}
      <div>
        <span className="muted">Projection</span>
        <div className="flex items-center gap-2 mt-0.5">
          <GlassPill className={`px-2 py-0.5 text-[10px] kicker ${tone}`}>
            {statusLabel}
          </GlassPill>
        </div>
        <p className="leading-relaxed text-ink mt-1">
          {projectionStatusExplanation(row.projection_status)}
        </p>
      </div>
      {row.wikidata_representation?.trim() && (
        <div>
          <span className="muted">How Wikidata represents this</span>
          <p className="leading-relaxed text-ink mt-0.5">
            {row.wikidata_representation.trim()}
          </p>
        </div>
      )}
      {row.notes?.trim() && (
        <div>
          <span className="muted">Why</span>
          <p className="leading-relaxed text-ink mt-0.5 whitespace-pre-wrap">
            {row.notes.trim()}
          </p>
        </div>
      )}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
        <div>
          <span className="muted">HMO nodes</span>{" "}
          <span className="font-mono text-ink">{row.hmo_node_count}</span>
        </div>
        <div>
          <span className="muted">Projected items</span>{" "}
          <span className="font-mono text-ink">{row.projected_item_count}</span>
        </div>
        {row.item_entity_type && (
          <div>
            <span className="muted">Item type</span>{" "}
            <span className="font-mono text-ink">{row.item_entity_type}</span>
          </div>
        )}
      </div>
      {row.wikidata_properties.length > 0 && (
        <div>
          <span className="muted">Wikidata properties</span>
          <ul className="mt-1 space-y-1">
            {row.wikidata_properties.map((pid) => (
              <li key={pid}>
                <a
                  href={wikidataPropertyHref(pid)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-biu-sky hover:underline font-mono"
                  onClick={(e) => e.stopPropagation()}
                >
                  {pid}
                </a>
                <span className="muted"> — {wikidataPropertyLabel(pid)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {row.class_uri && (
        <div className="text-[10px] muted border-t border-white/5 pt-2 break-all">
          {row.class_uri}
        </div>
      )}
    </ClickDetailPopover>
  );
}

/** Optional helper type for table-owned popover state. */
export type CoverageExplainAnchor = {
  row: HmoCoverageEntry;
  x: number;
  y: number;
};

export function useCoverageExplainPopover() {
  const [anchor, setAnchor] = useState<CoverageExplainAnchor | null>(null);
  return {
    anchor,
    open: (row: HmoCoverageEntry, x: number, y: number) => setAnchor({row, x, y}),
    close: () => setAnchor(null),
  };
}
