/**
 * ExportProjectDialog — full-project export modal.
 *
 *   - Multi-checkbox row for the five entity types (all checked by
 *     default).
 *   - "Include snapshots?" checkbox — when ticked, also calls the
 *     ``/export/snapshots`` endpoint after the main export.
 *   - "Include full event history?" — when ticked, also calls the
 *     ``/export/history`` endpoint (no entity filter).
 *   - "Export" button triggers the appropriate fetch(es) + download.
 *   - "Preparing export…" indicator while in-flight.
 *
 * Glass-themed (mirrors EntityEditModal). ``data-testid="export-
 * project-dialog"``.
 */

import { useEffect, useMemo, useState } from "react";

import {
  EXPORT_ENTITY_LABELS,
  EXPORT_ENTITY_TYPES,
  Export,
  type ExportEntityType,
} from "@/api/export";
import {Glass} from "@/components/glass";

export interface ExportProjectDialogProps {
  projectId: string;
  onClose: () => void;
}

interface CheckedMap {
  [k: string]: boolean;
}

function allChecked(): CheckedMap {
  const out: CheckedMap = {};
  for (const t of EXPORT_ENTITY_TYPES) out[t] = true;
  return out;
}

export function ExportProjectDialog({
  projectId,
  onClose,
}: ExportProjectDialogProps): JSX.Element {
  const [checked, setChecked] = useState<CheckedMap>(allChecked);
  const [includeSnapshots, setIncludeSnapshots] = useState(false);
  const [includeHistory, setIncludeHistory] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Close on Escape — matches the other glass modals' affordance.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !busy) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const selectedTypes = useMemo<ExportEntityType[]>(
    () => EXPORT_ENTITY_TYPES.filter((t) => checked[t]),
    [checked],
  );

  const noneSelected =
    selectedTypes.length === 0 && !includeSnapshots && !includeHistory;

  function toggle(t: ExportEntityType): void {
    setChecked((prev) => ({ ...prev, [t]: !prev[t] }));
  }

  function selectAll(): void {
    setChecked(allChecked());
  }

  function selectNone(): void {
    const empty: CheckedMap = {};
    for (const t of EXPORT_ENTITY_TYPES) empty[t] = false;
    setChecked(empty);
  }

  async function onExport(): Promise<void> {
    if (noneSelected) {
      setError("Choose at least one entity type, snapshots, or history.");
      return;
    }
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      if (selectedTypes.length > 0) {
        setStatus("Preparing export…");
        await Export.project(projectId, selectedTypes);
      }
      if (includeSnapshots) {
        setStatus("Preparing snapshots…");
        await Export.snapshots(projectId, {});
      }
      if (includeHistory) {
        setStatus("Preparing history…");
        await Export.history(projectId, {});
      }
      setStatus("Done.");
      // Small delay so the user can see the "Done." before the modal
      // closes on its own.
      setTimeout(() => onClose(), 400);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed.");
      setStatus(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      data-testid="export-project-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="export-project-dialog-title"
    >
      <Glass className="flex max-h-[88vh] w-full max-w-xl flex-col overflow-hidden">
        <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
          <div>
            <div className="kicker">Export project</div>
            <h3
              id="export-project-dialog-title"
              className="text-lg font-medium"
            >
              Download project data
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            data-testid="export-project-close"
            className="button-ghost h-8 px-3 text-xs"
          >
            Close
          </button>
        </div>

        <div className="space-y-4 overflow-auto p-4">
          <section className="space-y-2">
            <div className="flex items-center justify-between">
              <label className="kicker">Entity types</label>
              <div className="flex gap-1">
                <button
                  type="button"
                  onClick={selectAll}
                  disabled={busy}
                  className="button-ghost text-[10px] px-2 py-0.5"
                  data-testid="export-select-all"
                >
                  All
                </button>
                <button
                  type="button"
                  onClick={selectNone}
                  disabled={busy}
                  className="button-ghost text-[10px] px-2 py-0.5"
                  data-testid="export-select-none"
                >
                  None
                </button>
              </div>
            </div>
            <ul className="grid grid-cols-1 gap-1 sm:grid-cols-2">
              {EXPORT_ENTITY_TYPES.map((t) => (
                <li key={t}>
                  <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 hover:bg-white/[0.04]">
                    <input
                      type="checkbox"
                      checked={Boolean(checked[t])}
                      onChange={() => toggle(t)}
                      disabled={busy}
                      data-testid={`export-entity-type-${t}`}
                      className="accent-biu-sky"
                    />
                    <span className="text-sm">{EXPORT_ENTITY_LABELS[t]}</span>
                  </label>
                </li>
              ))}
            </ul>
          </section>

          <section className="space-y-1 border-t border-white/5 pt-3">
            <label className="kicker">Optional extras</label>
            <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 hover:bg-white/[0.04]">
              <input
                type="checkbox"
                checked={includeSnapshots}
                onChange={(e) => setIncludeSnapshots(e.target.checked)}
                disabled={busy}
                data-testid="export-include-snapshots"
                className="accent-biu-sky"
              />
              <span className="text-sm">
                Include snapshots
                <span className="muted block text-[11px]">
                  Point-in-time snapshots across all entity types.
                </span>
              </span>
            </label>
            <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 hover:bg-white/[0.04]">
              <input
                type="checkbox"
                checked={includeHistory}
                onChange={(e) => setIncludeHistory(e.target.checked)}
                disabled={busy}
                data-testid="export-include-history"
                className="accent-biu-sky"
              />
              <span className="text-sm">
                Include full event history
                <span className="muted block text-[11px]">
                  Every project-scoped change event — useful for audit.
                </span>
              </span>
            </label>
          </section>

          {status ? (
            <div
              className="rounded border border-white/10 bg-white/[0.04] px-3 py-2 text-xs"
              data-testid="export-project-status"
            >
              {status}
            </div>
          ) : null}
          {error ? (
            <div
              className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300"
              data-testid="export-project-error"
            >
              {error}
            </div>
          ) : null}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-white/10 px-4 py-3">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="button-ghost h-8 px-3 text-xs"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onExport}
            disabled={busy || noneSelected}
            data-testid="export-project-submit"
            className="button-primary h-8 px-3 text-xs"
          >
            {busy ? "Preparing export…" : "Export"}
          </button>
        </div>
      </Glass>
    </div>
  );
}
