import {useState} from "react";
import type {ValidationIssue} from "@/api/wikidataStudio";
import {Glass} from "@/components/glass";

interface ItemValidatorBadgeProps {
  issues: ValidationIssue[];
  localId?: string;
  /** When true, renders full chip list (for ItemPanel header).
   *  When false (default), renders a single dot for the sidebar. */
  expanded?: boolean;
}

export function ItemValidatorBadge({issues, localId, expanded = false}: ItemValidatorBadgeProps) {
  const [open, setOpen] = useState(false);
  if (issues.length === 0) return null;

  const hasError = issues.some((i) => i.severity === "error");

  if (expanded) {
    return (
      <div className="flex flex-col gap-1 mt-1">
        {issues.map((issue, idx) => (
          <div
            key={idx}
            className={`text-xs px-2 py-1 rounded flex items-start gap-1.5 ${
              issue.severity === "error"
                ? "badge-danger"
                : "badge-warn"
            }`}
          >
            <span className="shrink-0">{issue.severity === "error" ? "✕" : "⚠"}</span>
            <span>
              <span className="font-mono font-semibold">{issue.code}</span>
              {" — "}
              {issue.message}
            </span>
          </div>
        ))}
      </div>
    );
  }

  return (
    <span className="relative inline-flex items-center">
      <button
        type="button"
        data-testid={localId ? `wikidata-item-validator-badge-${localId}` : undefined}
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        title={issues.map((i) => `${i.code}: ${i.message}`).join("\n")}
        className={`w-2 h-2 rounded-full shrink-0 ${
          hasError ? "bg-red-400" : "bg-yellow-400"
        }`}
        aria-label={`${issues.length} validation issue${issues.length === 1 ? "" : "s"}`}
      />
      {open && (
        <Glass
          className="absolute left-3 top-0 z-50 w-64 shadow-xl rounded-lg p-2 space-y-1 text-xs"
          data-testid={localId ? `wikidata-item-validator-detail-${localId}` : undefined}
          onClick={(e) => e.stopPropagation()}
        >
          {issues.map((issue, idx) => (
            <div
              key={idx}
              className={`px-2 py-1 rounded ${
                issue.severity === "error"
                  ? "text-danger"
                  : "text-warn"
              }`}
            >
              <span className="font-mono font-semibold">{issue.code}</span>
              {": "}
              {issue.message}
            </div>
          ))}
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="block w-full text-right muted text-[10px] pt-0.5"
          >
            close
          </button>
        </Glass>
      )}
    </span>
  );
}
