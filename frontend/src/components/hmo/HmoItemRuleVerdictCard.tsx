import {useEffect, useState} from "react";

import {ApiError} from "@/api/client";
import {
  RuleVerify,
  type RuleMeta,
  type RuleVerifyEntity,
} from "@/api/ruleVerify";

const STATE_BADGE: Record<string, string> = {
  pass: "text-emerald-700 bg-emerald-500/10 border-emerald-500/30",
  fail: "text-danger bg-red-500/10 border-red-500/30",
  error: "text-amber-700 bg-amber-500/10 border-amber-500/30",
  not_relevant: "muted bg-white/5 border-white/10",
  unchecked: "muted bg-white/5 border-white/10",
};

function StateBadge({state}: {state: string}) {
  return (
    <span
      className={`inline-block rounded border px-1.5 py-0.5 text-[10px] font-medium ${STATE_BADGE[state] ?? STATE_BADGE.unchecked}`}
    >
      {state}
    </span>
  );
}

export interface HmoItemRuleVerdictCardProps {
  runId: string;
  localId: string;
}

/** Failed rule checks for one item, shown in the item detail drawer. */
export function HmoItemRuleVerdictCard({runId, localId}: HmoItemRuleVerdictCardProps) {
  const [entity, setEntity] = useState<RuleVerifyEntity | null>(null);
  const [catalog, setCatalog] = useState<RuleMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      RuleVerify.entity(runId, localId),
      RuleVerify.catalog(runId),
    ])
      .then(([ent, cat]) => {
        if (cancelled) return;
        setEntity(ent);
        setCatalog(cat);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof ApiError ? e.detail : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [localId, runId]);

  if (loading) {
    return (
      <section className="space-y-1 border-t border-white/5 pt-3" data-testid="hmo-item-rule-card">
        <h4 className="text-sm font-medium">Rule check</h4>
        <p className="muted text-xs" role="status">Loading rule results…</p>
      </section>
    );
  }

  const titleById = new Map(catalog.map((m) => [m.id, m.title]));
  const results = entity?.results ?? [];

  return (
    <section className="space-y-2 border-t border-white/5 pt-3" data-testid="hmo-item-rule-card">
      <h4 className="text-sm font-medium">Rule check</h4>
      {error && <p className="text-danger text-sm">{error}</p>}
      {!error && entity && (
        <>
          <div className="flex items-center gap-2 text-xs">
            <StateBadge state={entity.overall} />
            {entity.checked_at && (
              <span className="muted">checked {new Date(entity.checked_at).toLocaleString()}</span>
            )}
            {entity.upload_ready && results.length === 0 && (
              <span className="text-emerald-700">ready to upload</span>
            )}
          </div>
          {results.length === 0 && (entity.pass_count ?? 0) === 0 && (
            <p className="muted text-xs">No rule results recorded — run “Verify with rules”.</p>
          )}
          <div className="space-y-1 text-xs" data-testid={`hmo-item-rule-results-${localId}`}>
            {results.map((res) => (
              <div key={res.rule_id} className="flex items-start gap-2">
                <StateBadge state={res.state} />
                <span className="font-medium">{titleById.get(res.rule_id) ?? res.rule_id}</span>
                {res.field && <span className="muted">[{res.field}]</span>}
                {res.message && <span className="muted">{res.message}</span>}
              </div>
            ))}
          </div>
          {(entity.pass_count ?? 0) > 0 && (
            <p className="muted text-xs">+ {entity.pass_count} more rule(s) passed</p>
          )}
        </>
      )}
    </section>
  );
}
