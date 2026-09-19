import type {HmoStudioItem} from "@/api/hmoStudioItems";

const BADGE: Record<string, string> = {
  pass: "text-emerald-700 bg-emerald-500/10 border-emerald-500/30",
  fail: "text-danger bg-red-500/10 border-red-500/30",
  error: "text-amber-700 bg-amber-500/10 border-amber-500/30",
  not_relevant: "muted bg-white/5 border-white/10",
};

export function HmoItemRuleBadge({item}: {item: HmoStudioItem}) {
  const verdict = item.rule_verdict;
  if (!verdict) {
    return <span className="muted text-xs" title="Run Verify with rules on this run">not checked</span>;
  }
  const failCount = Number(verdict.fail_count ?? 0);
  const errCount = Number(verdict.error_count ?? 0);
  const state = failCount > 0 ? "fail" : errCount > 0 ? "error" : "pass";
  const titles = (verdict.failing_rules ?? []).slice(0, 5).join(", ");
  return (
    <span
      className={`inline-block rounded border px-1.5 py-0.5 text-[10px] font-medium ${BADGE[state]}`}
      title={failCount > 0 ? `Failing: ${titles}` : `${verdict.pass_count ?? 0} rules passed`}
      data-testid={`hmo-item-rule-${item.local_id}`}
    >
      {failCount > 0 ? `${state} (${failCount})` : errCount > 0 ? `${state} (${errCount})` : state}
    </span>
  );
}
