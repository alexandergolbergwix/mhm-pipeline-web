import {useState} from "react";

import {ClickDetailPopover} from "@/components/ClickDetailPopover";
import {GlassPill} from "@/components/glass";

interface ShaclIssue {
  severity: string;
  message: string;
  focus_node?: string;
}

export interface HmoItemShaclBadgeProps {
  issues: ShaclIssue[];
  localId?: string;
}

export function HmoItemShaclBadge({issues, localId}: HmoItemShaclBadgeProps) {
  const [popover, setPopover] = useState<{x: number; y: number} | null>(null);

  if (!issues.length) {
    return (
      <GlassPill className="px-2 py-0.5 text-[10px] kicker text-biu-sky">
        ok
      </GlassPill>
    );
  }

  const hasError = issues.some((i) => i.severity === "Error" || i.severity === "Violation");
  const tone = hasError ? "text-danger" : "text-warn";
  const label = hasError
    ? `${issues.length} error${issues.length === 1 ? "" : "s"}`
    : `${issues.length} warn`;

  return (
    <>
      <button
        type="button"
        className="cursor-pointer"
        data-testid={localId ? `hmo-item-shacl-badge-${localId}` : undefined}
        onClick={(e) => {
          e.stopPropagation();
          setPopover({x: e.clientX, y: e.clientY});
        }}
        title="Click to read validation issues"
      >
        <GlassPill className={`px-2 py-0.5 text-[10px] kicker ${tone} hover:brightness-110`}>
          {label}
        </GlassPill>
      </button>
      {popover && (
        <ClickDetailPopover
          x={popover.x}
          y={popover.y}
          title="Validation issues"
          testId={localId ? `hmo-item-shacl-detail-${localId}` : undefined}
          onClose={() => setPopover(null)}
        >
          {issues.map((issue, idx) => (
            <div
              key={idx}
              className={issue.severity === "Error" || issue.severity === "Violation" ? "text-danger" : "text-warn"}
            >
              <span className="font-semibold">{issue.severity}</span>
              {issue.focus_node ? (
                <span className="muted"> · {issue.focus_node}</span>
              ) : null}
              <div className="mt-0.5 leading-snug whitespace-pre-wrap break-words">{issue.message}</div>
            </div>
          ))}
        </ClickDetailPopover>
      )}
    </>
  );
}
