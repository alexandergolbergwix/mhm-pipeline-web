/**
 * AutoApproveRuleBuilder — modal rule builder for bulk-approving
 * entities by predicate.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { useDebounce } from "@/hooks/useDebounce";

import {
  AutoApproveRule, DEFAULT_AUTO_APPROVE_RULE,
  ENTITY_ROLES, ENTITY_SOURCES, ENTITY_TYPES,
  ExtractionApprovals,
} from "@/api/extractionApprovals";

export interface AutoApproveRuleBuilderProps {
  runId: string;
  initialRule?: Partial<AutoApproveRule>;
  onClose: () => void;
  onComplete: (approvedCount: number) => void;
}

function clonePartial(partial: Partial<AutoApproveRule>): AutoApproveRule {
  return {
    ...DEFAULT_AUTO_APPROVE_RULE,
    ...partial,
    sources:   partial.sources   ? [...partial.sources]   : [...DEFAULT_AUTO_APPROVE_RULE.sources],
    types:     partial.types     ? [...partial.types]     : [...DEFAULT_AUTO_APPROVE_RULE.types],
    not_roles: partial.not_roles ? [...partial.not_roles] : [...DEFAULT_AUTO_APPROVE_RULE.not_roles],
  };
}

export function AutoApproveRuleBuilder({
  runId, initialRule, onClose, onComplete,
}: AutoApproveRuleBuilderProps) {
  const [rule, setRule] = useState<AutoApproveRule>(() => clonePartial(initialRule ?? {}));
  const [preview, setPreview] = useState<number | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Debounce the server-side preview: the controls (slider, checkboxes)
  // stay bound to the live `rule` so they're instantly responsive, but the
  // /auto-approve/preview POST only fires after the user pauses — otherwise
  // dragging the confidence slider floods the backend with a request per
  // frame (each re-reads ner_results.json from disk).
  const debouncedRule = useDebounce(rule, 350);

  useEffect(() => {
    let cancelled = false;
    setPreviewing(true);
    ExtractionApprovals.previewAutoApprove(runId, debouncedRule)
      .then((res) => { if (!cancelled) { setPreview(res.matched); setError(null); } })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Could not preview rule.");
        setPreview(null);
      })
      .finally(() => { if (!cancelled) setPreviewing(false); });
    return () => { cancelled = true; };
  }, [runId, debouncedRule]);

  const toggle = useCallback(
    (key: "sources" | "types" | "not_roles", value: string) => {
      setRule((prev) => {
        const list = (prev[key] as readonly string[]) ?? [];
        const present = list.includes(value);
        const nextList = present
          ? list.filter((v) => v !== value)
          : [...list, value];
        // Cast through unknown because `AutoApproveRule[key]` is a
        // union of narrowly-typed string-literal arrays; the
        // generic-friendly form below produced over-constrained types
        // that TypeScript refused to unify.
        return { ...prev, [key]: nextList as unknown as never };
      });
    }, [],
  );

  const handleApply = useCallback(async () => {
    setApplying(true); setError(null);
    try {
      const res = await ExtractionApprovals.autoApprove(runId, rule);
      onComplete(res.approved); onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not apply rule.");
    } finally { setApplying(false); }
  }, [runId, rule, onClose, onComplete]);

  const previewLabel = useMemo(() => {
    if (previewing) return "Calculating…";
    if (preview === null) return "—";
    return `${preview} ${preview === 1 ? "entity" : "entities"} match`;
  }, [previewing, preview]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
         data-testid="auto-approve-modal"
         role="dialog" aria-modal="true">
      <div className="glass flex max-h-[88vh] w-full max-w-2xl flex-col overflow-hidden">
        <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
          <div>
            <div className="kicker">Auto-approve rule</div>
            <div className="text-sm muted">Bulk-approve entities that match every condition below.</div>
          </div>
          <button type="button" onClick={onClose}
                  data-testid="auto-approve-close"
                  className="button-ghost h-8 px-3 text-xs">Close</button>
        </div>
        <div className="flex-1 space-y-4 overflow-auto px-4 py-4">
          <div>
            <label className="kicker">
              Min confidence: <span className="text-ink" data-testid="min-confidence-value">{rule.min_confidence.toFixed(2)}</span>
            </label>
            <input type="range" min={0} max={1} step={0.05}
                   data-testid="min-confidence-slider"
                   value={rule.min_confidence}
                   onChange={(e) => setRule((p) => ({ ...p, min_confidence: Number(e.target.value) }))}
                   className="w-full accent-biu-sky" />
          </div>
          <div>
            <div className="kicker mb-1">Sources</div>
            <div className="flex flex-wrap gap-2">
              {ENTITY_SOURCES.map((s) => (
                <label key={s} className="glass-pill flex cursor-pointer items-center gap-1 text-xs">
                  <input type="checkbox"
                         data-testid={`rule-source-${s}`}
                         checked={rule.sources.includes(s)}
                         onChange={() => toggle("sources", s)}
                         className="h-3 w-3 accent-biu-sky" />
                  <span>{s}</span>
                </label>
              ))}
            </div>
          </div>
          <div>
            <div className="kicker mb-1">Types</div>
            <div className="flex flex-wrap gap-2">
              {ENTITY_TYPES.map((t) => (
                <label key={t} className="glass-pill flex cursor-pointer items-center gap-1 text-xs">
                  <input type="checkbox"
                         data-testid={`rule-type-${t}`}
                         checked={rule.types.includes(t)}
                         onChange={() => toggle("types", t)}
                         className="h-3 w-3 accent-biu-sky" />
                  <span>{t}</span>
                </label>
              ))}
            </div>
          </div>
          <div>
            <div className="kicker mb-1">Exclude roles</div>
            <div className="flex flex-wrap gap-2">
              {ENTITY_ROLES.map((r) => (
                <label key={r || "_blank"} className="glass-pill flex cursor-pointer items-center gap-1 text-xs">
                  <input type="checkbox"
                         data-testid={`rule-not-role-${r || "blank"}`}
                         checked={rule.not_roles.includes(r)}
                         onChange={() => toggle("not_roles", r)}
                         className="h-3 w-3 accent-biu-sky" />
                  <span>{r || "(none)"}</span>
                </label>
              ))}
            </div>
          </div>
          <div className="space-y-2">
            <label className="flex cursor-pointer items-center gap-2 text-xs text-ink">
              <input type="checkbox"
                     data-testid="rule-require-ai-pass"
                     checked={rule.require_ai_pass}
                     onChange={(e) => setRule((p) => ({ ...p, require_ai_pass: e.target.checked }))}
                     className="h-3 w-3 accent-biu-sky" />
              <span>Require AI verdict = pass</span>
            </label>
            <label className="flex cursor-pointer items-center gap-2 text-xs text-ink">
              <input type="checkbox"
                     data-testid="rule-respect-ai-fail"
                     checked={rule.respect_ai_fail}
                     onChange={(e) => setRule((p) => ({ ...p, respect_ai_fail: e.target.checked }))}
                     className="h-3 w-3 accent-biu-sky" />
              <span>Skip entities the AI flagged as wrong</span>
            </label>
          </div>
          {error ? (
            <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300"
                 data-testid="auto-approve-error">{error}</div>
          ) : null}
        </div>
        <div className="flex items-center justify-between border-t border-white/10 px-4 py-3">
          <div className="text-xs muted" data-testid="auto-approve-preview">
            Preview: <span className="text-ink">{previewLabel}</span>
          </div>
          <div className="flex items-center gap-2">
            <button type="button" onClick={onClose}
                    className="button-ghost h-8 px-3 text-xs" disabled={applying}>Cancel</button>
            <button type="button" onClick={handleApply}
                    data-testid="auto-approve-apply"
                    className="button-primary h-8 px-3 text-xs" disabled={applying || preview === 0}>
              {applying ? "Applying…" : "Apply"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
