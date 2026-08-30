import {useState} from "react";

import type {AiVerdict} from "@/api/extractionApprovals";
import {ClickDetailPopover} from "@/components/ClickDetailPopover";
import {AiVerdictPill} from "@/components/extraction/AiVerdictPill";

type HmoAiVerdict = AiVerdict & {suggested_fixes?: Array<Record<string, unknown>>};

function verdictTitle(overall: string | undefined): string {
  const o = String(overall ?? "").toLowerCase();
  if (o === "fail") return "Why AI flagged this as wrong";
  if (o === "abstain") return "Why AI is unsure";
  if (o === "partial") return "Why AI marked this as partly correct";
  if (o === "full" || o === "pass") return "Why AI thinks this looks right";
  if (o === "verification_failed") return "AI provider error";
  return "AI verdict details";
}

function isProviderError(verdict: HmoAiVerdict): boolean {
  return verdict.verification_status === "provider_error" || verdict.judge_failure === true;
}

function formatWhen(at: string | null | undefined): string | null {
  if (!at) return null;
  try {
    return new Date(at).toLocaleString();
  } catch {
    return at;
  }
}

export interface HmoItemAiVerdictBadgeProps {
  verdict: AiVerdict | null | undefined;
  localId?: string;
}

export function HmoItemAiVerdictBadge({verdict, localId}: HmoItemAiVerdictBadgeProps) {
  const [popover, setPopover] = useState<{x: number; y: number} | null>(null);

  if (!verdict) {
    return <AiVerdictPill verdict={null} />;
  }

  const v = verdict as HmoAiVerdict;
  const fixes = v.suggested_fixes ?? [];
  const when = formatWhen(v.judged_at);

  return (
    <>
      <button
        type="button"
        className="cursor-pointer text-left"
        title={isProviderError(v) ? "Provider error — click for details" : "Click for full AI explanation"}
        data-testid={localId ? `hmo-item-ai-verdict-${localId}` : undefined}
        onClick={(e) => {
          e.stopPropagation();
          setPopover({x: e.clientX, y: e.clientY});
        }}
      >
        <AiVerdictPill verdict={verdict} />
      </button>
      {popover && (
        <ClickDetailPopover
          x={popover.x}
          y={popover.y}
          title={isProviderError(v) ? "AI provider error" : verdictTitle(v.overall)}
          testId={localId ? `hmo-item-ai-verdict-detail-${localId}` : undefined}
          panelClassName="w-[min(420px,calc(100vw-1.5rem))] max-h-[min(420px,70vh)]"
          onClose={() => setPopover(null)}
        >
          <div>
            <span className="muted">Verdict</span>
            <div><AiVerdictPill verdict={verdict} size="md" /></div>
          </div>
          <div>
            <span className="muted">Explanation</span>
            <div className="leading-relaxed whitespace-pre-wrap break-words text-ink">
              {v.reasoning?.trim()
                ? v.reasoning
                : "No written explanation was stored for this verdict. Re-run Verify with AI to refresh it."}
            </div>
          </div>
          {fixes.length > 0 && (
            <div>
              <span className="muted">Suggested fixes ({fixes.length})</span>
              <ul className="list-disc pl-4 space-y-1 leading-snug">
                {fixes.slice(0, 8).map((fix, i) => (
                  <li key={i}>
                    {typeof fix.target === "string" ? <b>{fix.target}: </b> : null}
                    {typeof fix.value === "string"
                      ? fix.value
                      : typeof fix.proposed === "string"
                        ? fix.proposed
                        : JSON.stringify(fix)}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {(v.model || when) && (
            <div className="text-[10px] muted border-t border-white/5 pt-2">
              {v.model && <>Model: {v.model}</>}
              {v.model && when && " · "}
              {when && <>Judged {when}</>}
            </div>
          )}
        </ClickDetailPopover>
      )}
    </>
  );
}
