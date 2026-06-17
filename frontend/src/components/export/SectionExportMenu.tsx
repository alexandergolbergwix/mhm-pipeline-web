/**
 * SectionExportMenu — dropdown button for per-section exports.
 *
 * Renders a "Export ▾" glass-pill button that opens a small inline
 * popover listing available formats.  Each format triggers a
 * download via ``SectionExport``.
 *
 * Props:
 *   - ``section``         — which pipeline section ("extraction" | …)
 *   - ``runId``           — UUID of the run
 *   - ``availableFormats``— ordered list of formats to show
 *   - ``approvedOnly``    — passed through to the export endpoint
 *     for sections that support it (extraction, authority, wikidata-studio)
 *
 * Usage:
 * ```tsx
 * <SectionExportMenu
 *   section="extraction"
 *   runId={run.id}
 *   availableFormats={["json", "csv"]}
 *   approvedOnly={false}
 * />
 * ```
 */

import {useEffect, useRef, useState} from "react";

import {
  SectionExport,
  type ExtractionExportFormat,
  type AuthorityExportFormat,
  type RdfExportFormat,
  type WikibaseExportFormat,
  type WikidataExportFormat,
} from "@/api/sectionExport";
import {Glass} from "@/components/glass";

export type ExportSection =
  | "extraction"
  | "authority"
  | "rdf"
  | "wikibase"
  | "wikidata-studio";

export type ExportFormat = string; // "json" | "csv" | "ttl" | "nt"

interface SectionExportMenuProps {
  section: ExportSection;
  runId: string;
  availableFormats: ExportFormat[];
  approvedOnly?: boolean;
  className?: string;
}

const FORMAT_LABELS: Record<string, string> = {
  json: "JSON",
  csv:  "CSV",
  ttl:  "Turtle (.ttl)",
  nt:   "N-Triples (.nt)",
};

export function SectionExportMenu({
  section,
  runId,
  availableFormats,
  approvedOnly = false,
  className,
}: SectionExportMenuProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  async function doExport(fmt: ExportFormat) {
    setBusy(fmt);
    setError(null);
    try {
      switch (section) {
        case "extraction":
          await SectionExport.extraction(runId, fmt as ExtractionExportFormat, approvedOnly);
          break;
        case "authority":
          await SectionExport.authority(runId, fmt as AuthorityExportFormat, approvedOnly);
          break;
        case "rdf":
          await SectionExport.rdf(runId, fmt as RdfExportFormat);
          break;
        case "wikibase":
          await SectionExport.wikibase(runId, fmt as WikibaseExportFormat);
          break;
        case "wikidata-studio":
          await SectionExport.wikidataStudio(runId, fmt as WikidataExportFormat, approvedOnly);
          break;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setBusy(null);
      setOpen(false);
    }
  }

  const cls = className ?? "button-ghost text-sm inline-flex items-center gap-1";

  return (
    <div className="relative inline-block" ref={menuRef} data-testid="section-export-menu">
      <button
        type="button"
        onClick={() => { setOpen((o) => !o); setError(null); }}
        disabled={busy !== null}
        className={cls}
        aria-haspopup="true"
        aria-expanded={open}
        data-testid="section-export-menu-trigger"
      >
        {busy ? (
          <span className="animate-spin inline-block" aria-hidden="true">⟳</span>
        ) : (
          <span aria-hidden="true">↓</span>
        )}
        {busy ? `Exporting ${busy.toUpperCase()}…` : "Export"}
        <span aria-hidden="true" className="text-[10px]">▾</span>
      </button>

      {open && (
        <Glass className="absolute right-0 z-30 mt-1 min-w-[140px] rounded-md shadow-lg py-1" role="menu"
          data-testid="section-export-menu-dropdown">
          {availableFormats.map((fmt) => (
            <button
              key={fmt}
              type="button"
              role="menuitem"
              onClick={() => doExport(fmt)}
              disabled={busy !== null}
              className="w-full text-left px-3 py-1.5 text-sm hover:bg-white/10 disabled:opacity-50"
              data-testid={`section-export-format-${fmt}`}
            >
              {FORMAT_LABELS[fmt] ?? fmt.toUpperCase()}
            </button>
          ))}
        </Glass>
      )}

      {error && (
        <Glass className="absolute right-0 z-30 mt-1 min-w-[200px] rounded-md shadow-lg px-3 py-2 text-xs text-red-300" data-testid="section-export-error">
          {error}
        </Glass>
      )}
    </div>
  );
}
