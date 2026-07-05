import {GlassPill} from "@/components/glass";

interface ShaclIssue {
  severity: string;
  message: string;
}

export function HmoItemShaclBadge({issues}: {issues: ShaclIssue[]}) {
  if (!issues.length) {
    return (
      <GlassPill className="px-2 py-0.5 text-[10px] kicker text-biu-sky">
        ok
      </GlassPill>
    );
  }
  const hasError = issues.some((i) => i.severity === "Error" || i.severity === "Violation");
  const tone = hasError ? "text-danger" : "text-warn";
  const label = hasError ? `${issues.length} error` : `${issues.length} warn`;
  const title = issues.map((i) => `${i.severity}: ${i.message}`).join("\n");
  return (
    <span title={title}>
      <GlassPill className={`px-2 py-0.5 text-[10px] kicker ${tone}`}>
        {label}
      </GlassPill>
    </span>
  );
}
