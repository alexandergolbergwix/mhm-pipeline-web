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
}

export function HmoItemUploadOutcomeBadge({outcome, message, at}: HmoItemUploadOutcomeBadgeProps) {
  if (!outcome) {
    return <span className="muted text-xs">—</span>;
  }
  const label = OUTCOME_LABEL[outcome] ?? outcome;
  const tone = OUTCOME_TONE[outcome] ?? "muted";
  const titleParts = [message, at ? `at ${new Date(at).toLocaleString()}` : null].filter(Boolean);
  const title = titleParts.length ? titleParts.join(" — ") : undefined;
  return (
    <span title={title}>
      <GlassPill className={`px-2 py-0.5 text-[10px] kicker ${tone}`}>
        {label}
      </GlassPill>
    </span>
  );
}
