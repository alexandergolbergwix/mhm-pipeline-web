/**
 * HistoryTimeline — glass-themed vertical timeline over the per-entity
 * event-sourced audit log (``EntityHistory.timeline``).
 *
 * Rule W-16 glass styling: ``.glass`` container, sticky header,
 * monospaced rev numbers, themed op badges, hover-row highlight via
 * ``hover:bg-white/5``. Sibling components in
 * ``frontend/src/components/extraction/`` are the convention reference.
 *
 * Behaviour
 * - Loads the latest 200 events on mount.
 * - Per-row checkbox enables a "Diff" button when EXACTLY 2 are ticked.
 * - Per-row "Revert" button appears on op ∈ {patch, revert}; opens a
 *   confirm dialog with a free-text message input.
 * - "Older than 1000 events" footer link loads the archive-tier
 *   snapshots and appends them with reduced opacity.
 * - The diff modal is lazy-imported because another agent is shipping
 *   it in parallel — Suspense fallback keeps this component
 *   compilable even if that file lands a beat after this one.
 */

import { lazy, Suspense, useCallback, useEffect, useState } from "react";

import { ApiError } from "@/api/client";
import {
  EntityHistory,
  type EntityEventOp,
  type EntityEventRow,
  type EntitySnapshotRow,
  type EntityType,
} from "@/api/history";


const HistoryDiffModal = lazy(() =>
  import("@/components/history/HistoryDiffModal").then((m) => ({ default: m.default })),
);


export interface HistoryTimelineProps {
  projectId: string;
  entityType: EntityType;
  entityId: string;
  onClose?: () => void;
}


interface RevertDialogState {
  targetRev: number;
  message: string;
  submitting: boolean;
  error: string | null;
}


interface DiffModalState {
  fromRev: number;
  toRev: number;
}


const OP_BADGE: Record<EntityEventOp, { bg: string; fg: string; border: string; label: string }> = {
  create:   { bg: "rgba(120,200,140,0.18)", fg: "#7adf95", border: "rgba(120,200,140,0.35)", label: "create" },
  patch:    { bg: "rgba(127,196,255,0.18)", fg: "#77cce5", border: "rgba(127,196,255,0.35)", label: "patch" },
  revert:   { bg: "rgba(253,224,71,0.18)",  fg: "#fde047", border: "rgba(253,224,71,0.40)",  label: "revert" },
  snapshot: { bg: "rgba(255,255,255,0.06)", fg: "#9aa3ad", border: "rgba(255,255,255,0.18)", label: "snapshot" },
};


function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 1))}…`;
}


function formatAbsolute(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().replace("T", " ").slice(0, 19) + " UTC";
}


function formatTimeAgo(iso: string, nowMs: number): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const diffSec = Math.max(0, Math.round((nowMs - d.getTime()) / 1000));
  if (diffSec < 5)         return "just now";
  if (diffSec < 60)        return `${diffSec}s ago`;
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60)        return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24)         return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  if (diffDay < 30)        return `${diffDay}d ago`;
  const diffMon = Math.round(diffDay / 30);
  if (diffMon < 12)        return `${diffMon}mo ago`;
  const diffYr = Math.round(diffDay / 365);
  return `${diffYr}y ago`;
}


function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    return err.message || `HTTP ${err.status}`;
  }
  if (err instanceof Error) return err.message;
  return "Unknown error";
}


export function HistoryTimeline(props: HistoryTimelineProps) {
  const { projectId, entityType, entityId, onClose } = props;

  const [events, setEvents] = useState<EntityEventRow[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedRevs, setSelectedRevs] = useState<Set<number>>(new Set());

  const [revertDialog, setRevertDialog] = useState<RevertDialogState | null>(null);
  const [diffModal, setDiffModal] = useState<DiffModalState | null>(null);

  // Archive-tier snapshot rows ("Older than 1000 events"). They're
  // rendered with reduced opacity to signal coarse-grained
  // (3-per-day) sampling rather than full event fidelity.
  const [snapshots, setSnapshots] = useState<EntitySnapshotRow[] | null>(null);
  const [snapshotsLoading, setSnapshotsLoading] = useState<boolean>(false);
  const [snapshotsError, setSnapshotsError] = useState<string | null>(null);

  // ``nowMs`` is captured on render so every row uses a consistent
  // "now" anchor for its time-ago label. Refreshing the timeline
  // also refreshes this.
  const [nowMs, setNowMs] = useState<number>(() => Date.now());

  const loadTimeline = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await EntityHistory.timeline(projectId, entityType, entityId, 200);
      setEvents(rows);
      setNowMs(Date.now());
    } catch (err) {
      setError(describeError(err));
    } finally {
      setLoading(false);
    }
  }, [projectId, entityType, entityId]);

  useEffect(() => {
    void loadTimeline();
  }, [loadTimeline]);

  const toggleRev = useCallback((revNo: number) => {
    setSelectedRevs((prev) => {
      const next = new Set(prev);
      if (next.has(revNo)) next.delete(revNo);
      else next.add(revNo);
      return next;
    });
  }, []);

  const canDiff = selectedRevs.size === 2;

  const openDiff = useCallback(() => {
    if (selectedRevs.size !== 2) return;
    const [a, b] = Array.from(selectedRevs).sort((x, y) => x - y);
    setDiffModal({ fromRev: a, toRev: b });
  }, [selectedRevs]);

  const closeDiff = useCallback(() => setDiffModal(null), []);

  const openRevert = useCallback((targetRev: number) => {
    setRevertDialog({ targetRev, message: "", submitting: false, error: null });
  }, []);

  const closeRevert = useCallback(() => setRevertDialog(null), []);

  const submitRevert = useCallback(async () => {
    if (!revertDialog) return;
    setRevertDialog({ ...revertDialog, submitting: true, error: null });
    try {
      await EntityHistory.revert(projectId, {
        entity_type: entityType,
        entity_id: entityId,
        target_rev: revertDialog.targetRev,
        message: revertDialog.message.trim() || undefined,
      });
      setRevertDialog(null);
      setSelectedRevs(new Set());
      await loadTimeline();
    } catch (err) {
      setRevertDialog({
        targetRev: revertDialog.targetRev,
        message: revertDialog.message,
        submitting: false,
        error: describeError(err),
      });
    }
  }, [revertDialog, projectId, entityType, entityId, loadTimeline]);

  const loadSnapshots = useCallback(async () => {
    setSnapshotsLoading(true);
    setSnapshotsError(null);
    try {
      const rows = await EntityHistory.snapshots(projectId, entityType, entityId);
      setSnapshots(rows);
    } catch (err) {
      setSnapshotsError(describeError(err));
    } finally {
      setSnapshotsLoading(false);
    }
  }, [projectId, entityType, entityId]);

  // The /history endpoint guarantees ORDER BY rev_no DESC server-side
  // (confirmed in history.py::list_history). Consume directly without
  // a client-side sort; remove the defensive copy that scanned O(N)
  // on every render.
  const orderedEvents = events;

  const renderRow = (row: EntityEventRow) => {
    const badge = OP_BADGE[row.op];
    const isPatchOrRevert = row.op === "patch" || row.op === "revert";
    const checked = selectedRevs.has(row.rev_no);
    return (
      <li
        key={row.id}
        data-testid={`event-row-${row.rev_no}`}
        data-rev-no={row.rev_no}
        data-op={row.op}
        className="grid grid-cols-[40px_70px_90px_minmax(160px,1fr)_100px_minmax(120px,1.4fr)_80px] items-center gap-2 border-b border-white/5 px-3 py-2 text-sm text-ink hover:bg-white/5"
      >
        <input
          type="checkbox"
          aria-label={`Select revision ${row.rev_no} for diff`}
          data-testid={`event-checkbox-${row.rev_no}`}
          checked={checked}
          onChange={() => toggleRev(row.rev_no)}
          className="h-4 w-4 accent-biu-sky"
        />
        <span className="font-mono text-xs muted" title={`Revision number ${row.rev_no}`}>
          rev #{row.rev_no}
        </span>
        <span
          className="inline-flex w-fit items-center rounded-full px-2 py-0.5 text-[10px] uppercase tracking-wide"
          style={{ background: badge.bg, color: badge.fg, border: `1px solid ${badge.border}` }}
        >
          {badge.label}
        </span>
        {row.actor_email ? (
          <a
            href={`mailto:${row.actor_email}`}
            className="truncate text-xs hover:underline"
            title={row.actor_email}
          >
            {truncate(row.actor_email, 30)}
          </a>
        ) : (
          <span className="truncate text-xs muted">—</span>
        )}
        <span
          className="truncate text-xs muted"
          title={formatAbsolute(row.created_at)}
        >
          {formatTimeAgo(row.created_at, nowMs)}
        </span>
        <span
          className="truncate text-xs italic muted"
          title={row.message}
          dir="auto"
        >
          {row.message ? truncate(row.message, 60) : "—"}
        </span>
        <div className="flex justify-end">
          {isPatchOrRevert ? (
            <button
              type="button"
              data-testid={`revert-button-${row.rev_no}`}
              onClick={() => openRevert(row.rev_no)}
              className="button-ghost h-7 px-2 text-xs"
              title={`Revert to rev #${row.rev_no}`}
            >
              Revert
            </button>
          ) : null}
        </div>
      </li>
    );
  };

  const renderSnapshotRow = (row: EntitySnapshotRow) => {
    const badge = OP_BADGE.snapshot;
    const slotLabel = row.slot === 0 ? "00:00" : row.slot === 1 ? "08:00" : "16:00";
    return (
      <li
        key={`snap-${row.bucket}-${row.slot}-${row.rev_no}`}
        data-testid={`snapshot-row-${row.rev_no}`}
        data-rev-no={row.rev_no}
        data-bucket={row.bucket}
        data-slot={row.slot}
        className="grid grid-cols-[40px_70px_90px_minmax(160px,1fr)_100px_minmax(120px,1.4fr)_80px] items-center gap-2 border-b border-white/5 px-3 py-2 text-sm text-ink opacity-60 hover:opacity-80"
      >
        <span aria-hidden className="text-center text-xs muted">·</span>
        <span className="font-mono text-xs muted">rev #{row.rev_no}</span>
        <span
          className="inline-flex w-fit items-center rounded-full px-2 py-0.5 text-[10px] uppercase tracking-wide"
          style={{ background: badge.bg, color: badge.fg, border: `1px solid ${badge.border}` }}
        >
          archive
        </span>
        <span className="truncate text-xs muted">snapshot</span>
        <span className="truncate text-xs muted" title={formatAbsolute(row.created_at)}>
          {formatTimeAgo(row.created_at, nowMs)}
        </span>
        <span className="truncate text-xs italic muted">
          {row.bucket} · slot {slotLabel}
        </span>
        <span />
      </li>
    );
  };

  return (
    <section
      data-testid="history-timeline"
      data-entity-type={entityType}
      data-entity-id={entityId}
      className="glass flex max-h-[88vh] w-full flex-col overflow-hidden"
    >
      <header className="flex items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
        <div className="min-w-0">
          <div className="kicker">History</div>
          <div className="truncate text-sm muted">
            <span className="text-ink">{entityType}</span>
            <span className="muted"> · </span>
            <span className="font-mono text-xs">{entityId}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            data-testid="diff-button"
            onClick={openDiff}
            disabled={!canDiff}
            className="button-primary h-8 px-3 text-xs disabled:cursor-not-allowed disabled:opacity-40"
            title={canDiff ? "Diff the two selected revisions" : "Select exactly 2 revisions to enable diff"}
          >
            Diff{canDiff ? ` (${Array.from(selectedRevs).sort((a, b) => a - b).map((r) => `#${r}`).join(" → ")})` : ""}
          </button>
          <button
            type="button"
            onClick={() => void loadTimeline()}
            disabled={loading}
            className="button-ghost h-8 px-3 text-xs"
            data-testid="refresh-button"
            title="Reload timeline"
          >
            {loading ? "Refreshing…" : "Refresh"}
          </button>
          {onClose ? (
            <button
              type="button"
              onClick={onClose}
              className="button-ghost h-8 px-3 text-xs"
              data-testid="history-close"
            >
              Close
            </button>
          ) : null}
        </div>
      </header>

      {loading ? (
        <div
          className="border-b border-white/10 bg-white/5 px-4 py-2 text-xs muted"
          data-testid="history-loading-banner"
          role="status"
        >
          Loading history…
        </div>
      ) : null}
      {error ? (
        <div
          className="border-b border-red-500/40 bg-red-500/10 px-4 py-2 text-xs text-red-300"
          data-testid="history-error-banner"
          role="alert"
        >
          Failed to load history: {error}
        </div>
      ) : null}

      <div className="flex-1 overflow-auto">
        {orderedEvents.length === 0 && !loading && !error ? (
          <div
            className="px-4 py-8 text-center text-xs muted"
            data-testid="history-empty"
          >
            No events recorded yet for this entity.
          </div>
        ) : (
          <ul className="m-0 list-none p-0">
            {orderedEvents.map(renderRow)}
            {snapshots !== null && snapshots.length > 0 ? (
              <>
                <li
                  className="border-b border-white/10 bg-white/[0.02] px-3 py-1 text-[10px] uppercase tracking-wide muted"
                  data-testid="snapshot-divider"
                >
                  Archive tier · coarse-grained (3/day)
                </li>
                {snapshots.map(renderSnapshotRow)}
              </>
            ) : null}
          </ul>
        )}
      </div>

      <footer className="flex items-center justify-between gap-2 border-t border-white/10 px-4 py-2 text-xs muted">
        <span>
          {orderedEvents.length} event{orderedEvents.length === 1 ? "" : "s"} loaded
          {snapshots !== null && snapshots.length > 0
            ? ` · ${snapshots.length} archive row${snapshots.length === 1 ? "" : "s"}`
            : ""}
        </span>
        {snapshots === null ? (
          <button
            type="button"
            data-testid="snapshot-link"
            onClick={() => void loadSnapshots()}
            disabled={snapshotsLoading}
            className="hover:underline disabled:cursor-not-allowed disabled:opacity-50"
          >
            {snapshotsLoading ? "Loading archive…" : "Older than 1000 events"}
          </button>
        ) : (
          <span data-testid="snapshot-loaded">Archive loaded</span>
        )}
      </footer>
      {snapshotsError ? (
        <div
          className="border-t border-red-500/40 bg-red-500/10 px-4 py-2 text-xs text-red-300"
          data-testid="snapshot-error"
          role="alert"
        >
          Failed to load archive: {snapshotsError}
        </div>
      ) : null}

      {revertDialog ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          role="dialog"
          aria-modal="true"
          data-testid="revert-confirm-dialog"
          data-target-rev={revertDialog.targetRev}
        >
          <div className="glass flex w-full max-w-md flex-col overflow-hidden">
            <div className="border-b border-white/10 px-4 py-3">
              <div className="kicker">Revert</div>
              <div className="text-sm text-ink">
                Revert to rev #{revertDialog.targetRev}?
              </div>
              <div className="mt-1 text-xs muted">
                This appends a new event; nothing is destroyed.
              </div>
            </div>
            <div className="flex flex-col gap-2 p-4">
              <label className="block text-xs uppercase tracking-wide kicker" htmlFor="revert-message">
                Message (optional)
              </label>
              <textarea
                id="revert-message"
                data-testid="revert-message"
                value={revertDialog.message}
                onChange={(e) => setRevertDialog({ ...revertDialog, message: e.target.value })}
                rows={3}
                placeholder={`Restoring rev #${revertDialog.targetRev}`}
                className="input-glass w-full text-sm"
                disabled={revertDialog.submitting}
              />
              {revertDialog.error ? (
                <div
                  className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300"
                  data-testid="revert-error"
                  role="alert"
                >
                  {revertDialog.error}
                </div>
              ) : null}
              <div className="mt-2 flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={closeRevert}
                  disabled={revertDialog.submitting}
                  className="button-ghost h-8 px-3 text-xs"
                  data-testid="revert-cancel"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => void submitRevert()}
                  disabled={revertDialog.submitting}
                  className="button-primary h-8 px-3 text-xs"
                  data-testid="revert-submit"
                >
                  {revertDialog.submitting ? "Reverting…" : `Revert to #${revertDialog.targetRev}`}
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {diffModal ? (
        <Suspense
          fallback={
            <div
              className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
              data-testid="diff-modal-loading"
              role="status"
            >
              <div className="glass px-4 py-3 text-sm muted">Loading diff…</div>
            </div>
          }
        >
          <HistoryDiffModal
            projectId={projectId}
            entityType={entityType}
            entityId={entityId}
            fromRev={diffModal.fromRev}
            toRev={diffModal.toRev}
            onClose={closeDiff}
          />
        </Suspense>
      ) : null}
    </section>
  );
}


export default HistoryTimeline;
