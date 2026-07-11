import {useState} from "react";

import {ClickDetailPopover} from "@/components/ClickDetailPopover";
import {GlassPill} from "@/components/glass";

const OUTCOME_LABEL: Record<string, string> = {
  create: "created",
  adopt: "adopted",
  update: "updated",
  skip: "skipped",
  unchanged: "unchanged",
  failed: "failed",
  blocked: "blocked",
  would_block: "would block",
};

const OUTCOME_TONE: Record<string, string> = {
  create: "text-biu-sky",
  adopt: "text-biu-sky",
  update: "text-biu-sky",
  skip: "muted",
  unchanged: "muted",
  failed: "text-danger",
  blocked: "text-warn",
  would_block: "text-warn",
};

export interface UploadOutcomeBadgeProps {
  outcome?: string | null;
  message?: string | null;
  at?: string | null;
  localId?: string;
  /** Prefix for data-testid attributes (e.g. "hmo-item" or "wikidata-item"). */
  testIdPrefix?: string;
  /** When true, show timestamp under the pill in the table cell. */
  showDetail?: boolean;
  neverTriedLabel?: string;
}

function formatWhen(at: string | null | undefined): string | null {
  if (!at) return null;
  try {
    return new Date(at).toLocaleString();
  } catch {
    return at;
  }
}

export function UploadOutcomeBadge({
  outcome,
  message,
  at,
  localId,
  testIdPrefix = "item",
  showDetail = false,
  neverTriedLabel = "never tried",
}: UploadOutcomeBadgeProps) {
  const [popover, setPopover] = useState<{x: number; y: number} | null>(null);

  if (!outcome) {
    return (
      <span className="muted text-xs" title="No live upload attempt recorded for this item yet">
        {neverTriedLabel}
      </span>
    );
  }

  const label = OUTCOME_LABEL[outcome] ?? outcome;
  const tone = OUTCOME_TONE[outcome] ?? "muted";
  const when = formatWhen(at);
  const clickable = outcome === "failed" || outcome === "blocked" || outcome === "would_block" || Boolean(message?.trim());
  const badgeTestId = localId ? `${testIdPrefix}-upload-badge-${localId}` : undefined;
  const detailTestId = localId ? `${testIdPrefix}-upload-detail-${localId}` : undefined;
  const pill = (
    <GlassPill className={`px-2 py-0.5 text-[10px] kicker ${tone}${clickable ? " hover:brightness-110" : ""}`}>
      {label}
    </GlassPill>
  );

  return (
    <div className="space-y-0.5 max-w-[220px]">
      {clickable ? (
        <button
          type="button"
          className="cursor-pointer text-left"
          data-testid={badgeTestId}
          title="Click to read upload details"
          onClick={(e) => {
            e.stopPropagation();
            setPopover({x: e.clientX, y: e.clientY});
          }}
        >
          {pill}
        </button>
      ) : (
        pill
      )}
      {showDetail && when && (
        <div className="text-[10px] muted leading-tight">{when}</div>
      )}
      {popover && (
        <ClickDetailPopover
          x={popover.x}
          y={popover.y}
          title={outcome === "failed" ? "Upload failed" : "Last upload details"}
          testId={detailTestId}
          onClose={() => setPopover(null)}
        >
          <div>
            <span className="muted">Outcome</span>
            <div className={`font-medium ${tone}`}>{label}</div>
          </div>
          {when && (
            <div>
              <span className="muted">When</span>
              <div>{when}</div>
            </div>
          )}
          <div>
            <span className="muted">Reason</span>
            <div className={`leading-snug whitespace-pre-wrap break-words ${outcome === "failed" ? "text-danger" : ""}`}>
              {message?.trim() || "No detail message was recorded for this upload attempt."}
            </div>
          </div>
        </ClickDetailPopover>
      )}
    </div>
  );
}
