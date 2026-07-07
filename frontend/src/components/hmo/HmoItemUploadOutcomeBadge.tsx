import {GlassPill} from "@/components/glass";

const OUTCOME_LABEL: Record<string, string> = {
  create: "created",
  adopt: "adopted",
  update: "updated",
  skip: "skipped",
  unchanged: "unchanged",
  failed: "failed",
};

const OUTCOME_TONE: Record<string, string> = {
  create: "text-biu-sky",
  adopt: "text-biu-sky",
  update: "text-biu-sky",
  skip: "muted",
  unchanged: "muted",
  failed: "text-danger",
};

export interface HmoItemUploadOutcomeBadgeProps {
  outcome?: string | null;
  message?: string | null;
  at?: string | null;
  /** When true, show failure/adopt reason and timestamp under the pill (table cells). */
  showDetail?: boolean;
}

function formatWhen(at: string | null | undefined): string | null {
  if (!at) return null;
  try {
    return new Date(at).toLocaleString();
  } catch {
    return at;
  }
}

export function HmoItemUploadOutcomeBadge({
  outcome,
  message,
  at,
  showDetail = false,
}: HmoItemUploadOutcomeBadgeProps) {
  if (!outcome) {
    return (
      <span className="muted text-xs" title="No live upload attempt recorded for this item yet">
        never tried
      </span>
    );
  }
  const label = OUTCOME_LABEL[outcome] ?? outcome;
  const tone = OUTCOME_TONE[outcome] ?? "muted";
  const when = formatWhen(at);
  const titleParts = [message, when ? `at ${when}` : null].filter(Boolean);
  const title = titleParts.length ? titleParts.join(" — ") : undefined;
  const showMessage = showDetail && message && (outcome === "failed" || outcome === "adopt");
  return (
    <div className="space-y-0.5 max-w-[220px]" title={!showMessage ? title : undefined}>
      <GlassPill className={`px-2 py-0.5 text-[10px] kicker ${tone}`}>
        {label}
      </GlassPill>
      {showDetail && when && (
        <div className="text-[10px] muted leading-tight">{when}</div>
      )}
      {showMessage && (
        <div className={`text-[10px] leading-tight truncate ${outcome === "failed" ? "text-danger" : "muted"}`} title={message}>
          {message}
        </div>
      )}
    </div>
  );
}
