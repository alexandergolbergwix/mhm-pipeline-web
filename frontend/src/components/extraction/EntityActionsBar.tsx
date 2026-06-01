/**
 * EntityActionsBar — sticky toolbar above the entity table.
 *
 * Shows selection/visible/total counts and exposes bulk actions and
 * AI verification entry points. The AI verify buttons surface a scope
 * object so the parent can route the call through the eval-agent
 * subprocess (Rule W-13).
 */

import { useState } from "react";

import { Entity } from "@/api/extractionApprovals";

export interface EntityActionsBarProps {
  totalCount: number;
  visibleCount: number;
  selectedIds: Set<string>;
  visibleEntities: Entity[];
  onSelectAllVisible: () => void;
  onApproveSelected: () => void;
  onRejectSelected: () => void;
  onOpenAutoApprove: () => void;
  onVerifyScope: (scopeKind: "selection" | "all", entityIds: string[]) => void;
}

export function EntityActionsBar(props: EntityActionsBarProps) {
  const {
    totalCount, visibleCount, selectedIds, visibleEntities,
    onSelectAllVisible, onApproveSelected, onRejectSelected,
    onOpenAutoApprove, onVerifyScope,
  } = props;

  const [verifying, setVerifying] = useState<"selection" | "all" | null>(null);

  const selectionCount = selectedIds.size;
  const noSelection = selectionCount === 0;

  const handleVerify = (kind: "selection" | "all", ids: string[]) => {
    setVerifying(kind);
    try { onVerifyScope(kind, ids); }
    finally { window.setTimeout(() => setVerifying(null), 500); }
  };

  return (
    <div className="glass sticky top-0 z-10 flex flex-wrap items-center justify-between gap-3 px-3 py-2"
         data-testid="entity-actions-bar">
      <div className="text-xs muted" data-testid="entity-counts">
        <span className="text-ink" data-testid="count-selected">{selectionCount}</span> selected ·{" "}
        <span className="text-ink" data-testid="count-visible">{visibleCount}</span> visible ·{" "}
        <span className="text-ink" data-testid="count-total">{totalCount}</span> total
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="button-ghost h-8 px-3 text-xs"
                data-testid="btn-select-all-visible"
                onClick={onSelectAllVisible}>Select all visible</button>
        <button type="button" className="button-primary h-8 px-3 text-xs"
                data-testid="btn-approve-selected"
                disabled={noSelection} onClick={onApproveSelected}>Approve selected</button>
        <button type="button" className="button-ghost h-8 px-3 text-xs"
                data-testid="btn-reject-selected"
                disabled={noSelection} onClick={onRejectSelected}>Reject selected</button>
        <button type="button" className="button-ghost h-8 px-3 text-xs"
                data-testid="btn-auto-approve"
                onClick={onOpenAutoApprove}>Auto-approve…</button>
        <button type="button" className="button-ghost h-8 px-3 text-xs"
                data-testid="btn-verify-selected"
                disabled={noSelection || verifying === "selection"}
                onClick={() => handleVerify("selection", Array.from(selectedIds))}>
          {verifying === "selection" ? "Queueing…" : "Verify selected with AI"}
        </button>
        <button type="button" className="button-ghost h-8 px-3 text-xs"
                data-testid="btn-verify-all-visible"
                disabled={visibleCount === 0 || verifying === "all"}
                onClick={() => handleVerify("all", visibleEntities.map((e) => e.id))}>
          {verifying === "all" ? "Queueing…" : "Verify all visible with AI"}
        </button>
      </div>
    </div>
  );
}
