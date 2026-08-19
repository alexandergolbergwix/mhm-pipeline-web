/**
 * AiVerdictPill — inline renderer for the entity table's "AI verdict"
 * column.
 *
 * Five visual states keyed off ``verdict.overall``:
 *
 *   pass     → green   "looks right"
 *   partial  → amber   "partly"
 *   fail     → red     "wrong"
 *   abstain  → grey    "unsure"
 *   null/—   → grey    "—"            (not yet verified)
 *
 * On hover the native browser tooltip carries ``verdict.reasoning``
 * truncated to ~240 chars — enough for an at-a-glance read without
 * blocking the table. Clicking the pill fires ``onClick`` so the
 * parent can open the full verdict drawer / detail view.
 *
 * The component is intentionally tiny: it must be cheap to render at
 * the 1k-row scale Wave-2 targets. No state, no effects, no media
 * queries — just a styled span.
 *
 * TODO(integration): the canonical ``Entity.ai_verdict`` type lives
 * in ``@/api/extractionApprovals`` (table-half agent's file). When
 * that lands, replace the local ``AiVerdict`` interface here with
 *
 *     import type { Entity } from "@/api/extractionApprovals";
 *     verdict: Entity["ai_verdict"];
 *
 * and drop the local interface. The shape below is the documented
 * contract from the plan (Wave 1, ``ExtractionApproval.ai_verdict``
 * JSONB column) — once the table-half agent's types are merged the
 * shapes should already agree.
 */


// Canonical type lives in @/api/extractionApprovals (Rule W-13).
import type { AiVerdict } from "@/api/extractionApprovals";


export interface AiVerdictPillProps {
  /** ``null`` / undefined ⇒ render the "not yet verified" placeholder. */
  verdict: AiVerdict | null | undefined;
  /** Optional click handler — parent typically opens the detail view. */
  onClick?: () => void;
  size?:    "sm" | "md";
  /** True while this row is in an active AI-verify scope and has no verdict yet. */
  judging?: boolean;
}


type Bucket = "pass" | "partial" | "fail" | "abstain" | "verification_failed" | "unknown" | "judging";


/** Map ``verdict.overall`` onto one of the five visual buckets.
 *
 *  ``full`` and ``pass`` collapse to the same bucket because the
 *  eval-agent has used both names historically — pinning to the
 *  newer ``full`` while still accepting ``pass`` keeps old verdicts
 *  rendering correctly.
 */
function bucketFor(verdict: AiVerdict | null | undefined, judging?: boolean): Bucket {
  if (judging && !verdict) return "judging";
  if (!verdict) return "unknown";
  const o = String(verdict.overall ?? "").toLowerCase();
  if (o === "full" || o === "pass") return "pass";
  if (o === "partial") return "partial";
  if (o === "fail")    return "fail";
  if (o === "abstain") return "abstain";
  if (o === "verification_failed") return "verification_failed";
  return "unknown";
}


const _PALETTE: Record<Bucket, { bg: string; border: string; fg: string; label: string; glyph: string }> = {
  pass:                { bg: "rgba(127, 196, 255, 0.16)", border: "rgba(127, 196, 255, 0.45)",
                         fg: "#77cce5", label: "looks right",       glyph: "✓" },
  partial:             { bg: "rgba(253, 224, 71, 0.16)",  border: "rgba(253, 224, 71, 0.45)",
                         fg: "#fde047", label: "partly",            glyph: "~" },
  fail:                { bg: "rgba(248, 113, 113, 0.16)", border: "rgba(248, 113, 113, 0.45)",
                         fg: "#fca5a5", label: "wrong",             glyph: "✗" },
  abstain:             { bg: "rgba(255, 255, 255, 0.06)", border: "rgba(255, 255, 255, 0.20)",
                         fg: "var(--muted)", label: "unsure",       glyph: "?" },
  verification_failed: { bg: "rgba(253, 186, 116, 0.16)", border: "rgba(253, 186, 116, 0.45)",
                         fg: "#fb923c", label: "check failed",      glyph: "⚠" },
  judging:             { bg: "rgba(127, 196, 255, 0.10)", border: "rgba(127, 196, 255, 0.30)",
                         fg: "#77cce5", label: "judging…",          glyph: "…" },
  unknown:             { bg: "transparent",                border: "rgba(255, 255, 255, 0.12)",
                         fg: "var(--muted)", label: "—",            glyph: "—" },
};


/** Truncate to ~240 characters at a word boundary so the native
 *  browser tooltip stays readable but still includes most reasoning. */
function truncateReasoning(s: string | null | undefined, max = 240): string {
  if (!s) return "";
  const trimmed = s.trim();
  if (trimmed.length <= max) return trimmed;
  const cut = trimmed.slice(0, max);
  const lastSpace = cut.lastIndexOf(" ");
  const head = lastSpace > max * 0.6 ? cut.slice(0, lastSpace) : cut;
  return `${head}…`;
}


export function AiVerdictPill(props: AiVerdictPillProps) {
  const { verdict, onClick, size = "sm", judging = false } = props;
  const bucket = bucketFor(verdict, judging);
  const tone   = _PALETTE[bucket];

  const reasoning = truncateReasoning(verdict?.reasoning);
  const tooltip   = bucket === "judging"
    ? "AI is judging this item (cache hit skips a fresh LLM call)"
    : reasoning
      ? `${tone.label}\n\n${reasoning}`
      : tone.label;

  const padY = size === "md" ? "py-1"   : "py-0.5";
  const padX = size === "md" ? "px-2.5" : "px-2";
  const text = size === "md" ? "text-[11px]" : "text-[10px]";

  const isInteractive = !!onClick && bucket !== "unknown" && bucket !== "judging";

  const inner = (
    <>
      <span aria-hidden style={{ marginRight: 4 }}>{tone.glyph}</span>
      {tone.label}
    </>
  );

  // Render as <button> when interactive, <span> otherwise — keeps the
  // markup semantically correct (and lets keyboard users tab to it).
  if (isInteractive) {
    return (
      <button
        type="button"
        onClick={onClick}
        title={tooltip}
        className={`inline-flex items-center rounded-full font-medium transition active:scale-[0.97] ${padY} ${padX} ${text}`}
        style={{
          background: tone.bg,
          border:     `1px solid ${tone.border}`,
          color:      tone.fg,
          lineHeight: 1.1,
        }}
        data-verdict={bucket}
      >
        {inner}
      </button>
    );
  }
  return (
    <span
      title={tooltip}
      className={`inline-flex items-center rounded-full font-medium ${padY} ${padX} ${text}${bucket === "judging" ? " animate-pulse" : ""}`}
      style={{
        background: tone.bg,
        border:     `1px solid ${tone.border}`,
        color:      tone.fg,
        lineHeight: 1.1,
      }}
      data-verdict={bucket}
    >
      {inner}
    </span>
  );
}
