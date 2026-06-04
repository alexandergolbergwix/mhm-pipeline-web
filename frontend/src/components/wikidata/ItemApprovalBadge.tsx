interface ItemApprovalBadgeProps {
  approved: boolean | null | undefined;
  onToggle: () => void;
  loading: boolean;
  /** When true, shows full text label (for ItemPanel header).
   *  When false (default), shows compact icon for sidebar. */
  expanded?: boolean;
}

export function ItemApprovalBadge({
  approved,
  onToggle,
  loading,
  expanded = false,
}: ItemApprovalBadgeProps) {
  if (loading) {
    return (
      <span
        className={`inline-flex items-center gap-1 text-xs muted ${
          expanded ? "px-2 py-1" : ""
        }`}
        title="Saving…"
      >
        <span className="animate-spin inline-block w-3 h-3 border border-current border-t-transparent rounded-full" />
        {expanded && <span>Saving…</span>}
      </span>
    );
  }

  if (expanded) {
    return (
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); onToggle(); }}
        className={`inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-full border transition ${
          approved === true
            ? "bg-green-500/15 text-green-300 border-green-500/30 hover:bg-green-500/25"
            : "muted border-white/10 hover:text-ink hover:border-white/20"
        }`}
        title={approved === true ? "Click to un-approve" : "Click to approve"}
      >
        {approved === true ? (
          <>
            <span>✓</span>
            <span>Approved</span>
          </>
        ) : (
          <>
            <span>○</span>
            <span>Not reviewed</span>
          </>
        )}
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={(e) => { e.stopPropagation(); onToggle(); }}
      title={approved === true ? "Approved — click to un-approve" : "Not reviewed — click to approve"}
      className={`shrink-0 text-[11px] transition ${
        approved === true ? "text-green-400" : "muted hover:text-ink"
      }`}
    >
      {approved === true ? "✓" : "○"}
    </button>
  );
}
