/**
 * HistoryDiffModal — glass-themed dialog showing the diff between two
 * revisions of an entity.
 *
 * Fetches an RFC 6902 JSON-Patch payload from
 * ``EntityHistory.diff(projectId, entityType, entityId, fromRev, toRev)``
 * and renders three sections:
 *
 *   1. **Patch** — colour-coded list of operations.
 *   2. **Before** — JSON tree of the entity at ``fromRev``.
 *   3. **After**  — JSON tree of the entity at ``toRev``.
 *
 * The "Before" and "After" trees receive the patch ops' ``path`` values
 * as ``highlightPaths`` so a curator can spot the changed fields at a
 * glance.
 *
 * Accessibility:
 *   - ``role="dialog"`` + ``aria-modal="true"`` + ``aria-labelledby``.
 *   - ESC closes; click on the backdrop closes; the modal body itself
 *     stops propagation so an inner click does not bubble through.
 *   - Focus is trapped inside the modal via ``useFocusTrap``.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  EntityHistory,
  type EntityDiffPayload,
  type EntityPatchOp,
  type EntityType,
} from "@/api/history";
import { JsonTreeViewer } from "@/components/glass/JsonTreeViewer";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { ApiError } from "@/api/client";
import {Glass} from "@/components/glass";
import {useGlassOverlayLifecycle} from "@/hooks/useGlassOverlayLifecycle";

export interface HistoryDiffModalProps {
  projectId: string;
  entityType: EntityType;
  entityId: string;
  fromRev: number;
  toRev: number;
  onClose: () => void;
}

type PatchOpName = EntityPatchOp["op"];

const VALUE_TRUNCATE_AT = 120;

const OP_PILL_STYLES: Record<PatchOpName, string> = {
  add:     "badge-success px-1.5 py-0.5 rounded text-xs font-medium",
  remove:  "badge-danger px-1.5 py-0.5 rounded text-xs font-medium",
  replace: "badge-warn px-1.5 py-0.5 rounded text-xs font-medium",
  move:    "surface-inset text-subtle border border-[var(--line)] px-1.5 py-0.5 rounded text-xs font-medium",
  copy:    "surface-inset text-subtle border border-[var(--line)] px-1.5 py-0.5 rounded text-xs font-medium",
  test:    "surface-inset text-subtle border border-[var(--line)] px-1.5 py-0.5 rounded text-xs font-medium",
};

function formatValue(value: unknown): { text: string; truncated: boolean } {
  if (value === undefined) return { text: "—", truncated: false };
  let rendered: string;
  try {
    rendered = JSON.stringify(value);
  } catch {
    rendered = String(value);
  }
  if (rendered === undefined) rendered = String(value);
  if (rendered.length <= VALUE_TRUNCATE_AT) {
    return { text: rendered, truncated: false };
  }
  return { text: `${rendered.slice(0, VALUE_TRUNCATE_AT)}…`, truncated: true };
}

function collectHighlightPaths(patch: ReadonlyArray<EntityPatchOp>): string[] {
  const set = new Set<string>();
  for (const op of patch) {
    if (op.path) set.add(op.path);
    if (op.from) set.add(op.from);
  }
  return Array.from(set);
}

interface PatchOpRowProps {
  op: EntityPatchOp;
  index: number;
}

function PatchOpRow({ op, index }: PatchOpRowProps): JSX.Element {
  const pillClass = OP_PILL_STYLES[op.op];
  const { text: valueText, truncated } = formatValue(op.value);
  const showFrom = op.from !== undefined && op.from !== "";

  return (
    <li
      data-testid={`diff-patch-op-${index}`}
      className="flex flex-wrap items-start gap-2 rounded border border-white/10 bg-white/5 px-2 py-1.5 text-xs"
    >
      <span
        className={`inline-flex shrink-0 items-center rounded px-2 py-0.5 font-semibold uppercase tracking-wide ${pillClass}`}
      >
        {op.op}
      </span>
      <span className="break-all font-mono text-subtle">{op.path || "/"}</span>
      {showFrom && (
        <span className="break-all font-mono text-faint">
          <span className="kicker mr-1">from</span>
          {op.from}
        </span>
      )}
      {op.value !== undefined && (
        <span
          className="break-all font-mono text-string"
          title={truncated ? JSON.stringify(op.value) : undefined}
        >
          {valueText}
        </span>
      )}
    </li>
  );
}

export default function HistoryDiffModal(
  props: HistoryDiffModalProps,
): JSX.Element {
  const { projectId, entityType, entityId, fromRev, toRev, onClose } = props;
  const modalRef = useRef<HTMLDivElement>(null);
  const [diff, setDiff] = useState<EntityDiffPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  useFocusTrap(true, modalRef);
  useGlassOverlayLifecycle(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setDiff(null);
    EntityHistory.diff(projectId, entityType, entityId, fromRev, toRev)
      .then((payload) => {
        if (cancelled) return;
        setDiff(payload);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError) {
          setError(`Could not load diff (${err.status}): ${err.detail}`);
        } else if (err instanceof Error) {
          setError(err.message);
        } else {
          setError("Could not load diff.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, entityType, entityId, fromRev, toRev]);

  useEffect(() => {
    function handleKey(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose]);

  const headerId = "history-diff-modal-title";
  // Memoised on `diff` so the O(patch) walk doesn't re-run on every
  // unrelated re-render (keydown handlers, focus-trap churn, etc.).
  const highlightPaths = useMemo(
    () => (diff ? collectHighlightPaths(diff.patch) : []),
    [diff],
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      data-testid="diff-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby={headerId}
      onClick={onClose}
    >
      <Glass variant="modal" refraction={false} className="flex max-h-[88vh] w-full max-w-5xl flex-col overflow-hidden" ref={modalRef}
        
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
          <div>
            <div className="kicker">Diff</div>
            <h2 id={headerId} className="text-base font-semibold text-ink">
              {`Diff: rev #${fromRev} → rev #${toRev}`}
            </h2>
            <div className="text-xs muted">
              <span className="font-mono">{entityType}</span>
              {" · "}
              <span className="font-mono">{entityId}</span>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            data-testid="diff-modal-close"
            className="button-ghost h-8 px-3 text-xs"
          >
            Close
          </button>
        </div>

        <div className="flex-1 overflow-auto p-4">
          {loading && (
            <div className="muted text-sm" data-testid="diff-modal-loading">
              Loading diff…
            </div>
          )}

          {!loading && error !== null && (
            <div
              className="rounded border border-[var(--danger-border)] badge-danger px-3 py-2 text-sm"
              role="alert"
              data-testid="diff-modal-error"
            >
              {error}
            </div>
          )}

          {!loading && error === null && diff !== null && (
            <div className="flex flex-col gap-5">
              <section>
                <div className="kicker mb-2">Patch</div>
                {diff.patch.length === 0 ? (
                  <div className="muted text-sm" data-testid="diff-patch-empty">
                    No changes between these revisions.
                  </div>
                ) : (
                  <ul
                    data-testid="diff-patch-list"
                    className="flex flex-col gap-1.5"
                  >
                    {diff.patch.map((op, index) => (
                      <PatchOpRow key={index} op={op} index={index} />
                    ))}
                  </ul>
                )}
              </section>

              <section>
                <div className="kicker mb-2">Before</div>
                <div data-testid="diff-before">
                  <JsonTreeViewer
                    value={diff.before}
                    rootLabel="before"
                    highlightPaths={highlightPaths}
                    initiallyOpenDepth={1}
                  />
                </div>
              </section>

              <section>
                <div className="kicker mb-2">After</div>
                <div data-testid="diff-after">
                  <JsonTreeViewer
                    value={diff.after}
                    rootLabel="after"
                    highlightPaths={highlightPaths}
                    initiallyOpenDepth={1}
                  />
                </div>
              </section>
            </div>
          )}
        </div>
      </Glass>
    </div>
  );
}
