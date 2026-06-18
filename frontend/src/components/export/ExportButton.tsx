/**
 * ExportButton — small glass-pill button that triggers an export.
 *
 * Two modes:
 *   - ``mode="project"`` — renders "📥 Export…" and opens the
 *     <ExportProjectDialog> for the full-project flow. ``entityType``
 *     and ``entityId`` are ignored.
 *   - ``mode="single-entity"`` — renders "📥 Export history" and
 *     calls ``Export.history({ entity_type, entity_id })`` directly.
 *     Both ``entityType`` and ``entityId`` are required here.
 *
 * The button mirrors the styling of the project header's other
 * ``button-ghost text-sm`` actions (see ProjectDetail.tsx).
 */

import { useState } from "react";
import { createPortal } from "react-dom";

import { Export, type ExportEntityType } from "@/api/export";
import { ExportProjectDialog } from "@/components/export/ExportProjectDialog";

interface BaseProps {
  projectId: string;
  className?: string;
}

interface ProjectModeProps extends BaseProps {
  mode: "project";
  entityType?: never;
  entityId?: never;
}

interface SingleEntityModeProps extends BaseProps {
  mode: "single-entity";
  entityType: ExportEntityType;
  entityId: string;
}

export type ExportButtonProps = ProjectModeProps | SingleEntityModeProps;

export function ExportButton(props: ExportButtonProps): JSX.Element {
  const { projectId, className, mode } = props;
  const [dialogOpen, setDialogOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const cls =
    className ??
    "button-ghost text-sm inline-flex items-center gap-1";

  if (mode === "project") {
    return (
      <>
        <button
          type="button"
          onClick={() => setDialogOpen(true)}
          className={cls}
          data-testid="export-button-project"
          aria-label="Export project data"
        >
          <span aria-hidden="true">📥</span> Export…
        </button>
        {dialogOpen
          ? createPortal(
              <ExportProjectDialog
                projectId={projectId}
                onClose={() => setDialogOpen(false)}
              />,
              document.body,
            )
          : null}
      </>
    );
  }

  // single-entity mode
  async function onClick() {
    setBusy(true);
    setError(null);
    try {
      await Export.history(projectId, {
        entity_type: props.entityType,
        entity_id:   props.entityId,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="inline-flex items-center gap-2">
      <button
        type="button"
        onClick={onClick}
        disabled={busy}
        className={cls}
        data-testid="export-button-history"
        aria-label="Export entity history"
      >
        <span aria-hidden="true">📥</span>{" "}
        {busy ? "Preparing…" : "Export history"}
      </button>
      {error ? (
        <span className="text-danger text-xs" data-testid="export-button-error">
          {error}
        </span>
      ) : null}
    </span>
  );
}
