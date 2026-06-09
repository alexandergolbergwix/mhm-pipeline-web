/**
 * AuthorityAutoApproveRuleBuilder — modal rule builder for bulk-approving
 * authority candidates by predicate.
 *
 * Mirrors AutoApproveRuleBuilder from AI Extraction, adapted for authority:
 *  - Confidence levels (high / medium / low)
 *  - Sources (mazal / viaf / wikidata / kima) — empty = any
 *  - Entity kind (person / place) — empty = any
 *  - Min source count (1–4; ≥2 = cross-source)
 *  - Require AI pass / Skip AI-flagged toggles
 *  - Live preview count (debounced 350ms)
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError } from "@/api/client";
import { useDebounce } from "@/hooks/useDebounce";
import { Runs, type AuthorityAutoApproveRule } from "@/api/runs";

export type { AuthorityAutoApproveRule };

const DEFAULT_RULE: AuthorityAutoApproveRule = {
  confidence_levels: ["high", "medium", "low"],
  sources:           [],
  entity_kinds:      [],
  min_source_count:  1,
  require_ai_pass:   false,
  respect_ai_fail:   true,
};

const ALL_SOURCES = ["mazal", "viaf", "wikidata", "kima"] as const;
const ALL_KINDS   = ["person", "place"] as const;
const ALL_CONFS   = ["high", "medium", "low"] as const;

export interface AuthorityAutoApproveRuleBuilderProps {
  runId:      string;
  onClose:    () => void;
  onComplete: (approvedCount: number) => void;
}

export function AuthorityAutoApproveRuleBuilder({
  runId, onClose, onComplete,
}: AuthorityAutoApproveRuleBuilderProps) {
  const [rule, setRule] = useState<AuthorityAutoApproveRule>(() => ({ ...DEFAULT_RULE }));
  const [preview, setPreview] = useState<number | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Debounce the preview POST so fast checkbox clicks don't flood the server.
  const debouncedRule = useDebounce(rule, 350);

  useEffect(() => {
    let cancelled = false;
    setPreviewing(true);
    Runs.previewAuthorityAutoApprove(runId, debouncedRule)
      .then((res) => {
        if (!cancelled) { setPreview(res.matched); setError(null); }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.detail : "Could not preview rule.");
        setPreview(null);
      })
      .finally(() => { if (!cancelled) setPreviewing(false); });
    return () => { cancelled = true; };
  }, [runId, debouncedRule]);

  function toggleConf(v: "high" | "medium" | "low") {
    setRule((p) => ({
      ...p,
      confidence_levels: p.confidence_levels.includes(v)
        ? p.confidence_levels.filter((x) => x !== v)
        : [...p.confidence_levels, v],
    }));
  }

  function toggleSource(s: string) {
    setRule((p) => ({
      ...p,
      sources: p.sources.includes(s)
        ? p.sources.filter((x) => x !== s)
        : [...p.sources, s],
    }));
  }

  function toggleKind(k: string) {
    setRule((p) => ({
      ...p,
      entity_kinds: p.entity_kinds.includes(k)
        ? p.entity_kinds.filter((x) => x !== k)
        : [...p.entity_kinds, k],
    }));
  }

  const handleApply = useCallback(async () => {
    setApplying(true); setError(null);
    try {
      const res = await Runs.applyAuthorityAutoApprove(runId, rule);
      onComplete(res.approved);
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not apply rule.");
    } finally {
      setApplying(false);
    }
  }, [runId, rule, onClose, onComplete]);

  const previewLabel = useMemo(() => {
    if (previewing) return "Calculating…";
    if (preview === null) return "—";
    return `${preview} ${preview === 1 ? "candidate" : "candidates"} will be approved`;
  }, [previewing, preview]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
         data-testid="authority-auto-approve-modal"
         role="dialog" aria-modal="true">
      <div className="glass flex max-h-[88vh] w-full max-w-2xl flex-col overflow-hidden">

        <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
          <div>
            <div className="kicker">Auto-approve rule</div>
            <div className="text-sm muted">Bulk-approve authority candidates that match every condition below.</div>
          </div>
          <button type="button" onClick={onClose}
                  data-testid="authority-auto-approve-close"
                  className="button-ghost h-8 px-3 text-xs">Close</button>
        </div>

        <div className="flex-1 space-y-4 overflow-auto px-4 py-4">

          {/* Confidence levels */}
          <div>
            <div className="kicker mb-1">Confidence</div>
            <div className="flex flex-wrap gap-2">
              {ALL_CONFS.map((c) => (
                <label key={c} className="glass-pill flex cursor-pointer items-center gap-1 text-xs">
                  <input type="checkbox"
                         data-testid={`rule-conf-${c}`}
                         checked={rule.confidence_levels.includes(c)}
                         onChange={() => toggleConf(c)}
                         className="h-3 w-3 accent-biu-sky" />
                  <span>{c}</span>
                </label>
              ))}
            </div>
          </div>

          {/* Sources */}
          <div>
            <div className="kicker mb-1">
              Sources{" "}
              <span className="muted font-normal normal-case text-xs">(empty = any source)</span>
            </div>
            <div className="flex flex-wrap gap-2">
              {ALL_SOURCES.map((s) => (
                <label key={s} className="glass-pill flex cursor-pointer items-center gap-1 text-xs">
                  <input type="checkbox"
                         data-testid={`rule-source-${s}`}
                         checked={rule.sources.includes(s)}
                         onChange={() => toggleSource(s)}
                         className="h-3 w-3 accent-biu-sky" />
                  <span>{s}</span>
                </label>
              ))}
            </div>
          </div>

          {/* Entity kind */}
          <div>
            <div className="kicker mb-1">
              Entity kind{" "}
              <span className="muted font-normal normal-case text-xs">(empty = any kind)</span>
            </div>
            <div className="flex flex-wrap gap-2">
              {ALL_KINDS.map((k) => (
                <label key={k} className="glass-pill flex cursor-pointer items-center gap-1 text-xs">
                  <input type="checkbox"
                         data-testid={`rule-kind-${k}`}
                         checked={rule.entity_kinds.includes(k)}
                         onChange={() => toggleKind(k)}
                         className="h-3 w-3 accent-biu-sky" />
                  <span>{k}</span>
                </label>
              ))}
            </div>
          </div>

          {/* Min source count */}
          <div>
            <label className="kicker">
              Min source count:{" "}
              <span className="text-ink" data-testid="rule-min-source-count-value">
                {rule.min_source_count}
              </span>
              {rule.min_source_count >= 2 && (
                <span className="text-biu-sky ml-2 text-xs">(cross-source agreement)</span>
              )}
            </label>
            <input type="range" min={1} max={4} step={1}
                   data-testid="rule-min-source-count-slider"
                   value={rule.min_source_count}
                   onChange={(e) =>
                     setRule((p) => ({ ...p, min_source_count: Number(e.target.value) }))
                   }
                   className="w-full accent-biu-sky" />
            <div className="flex justify-between text-[10px] muted mt-0.5">
              <span>1 (any)</span><span>2</span><span>3</span><span>4</span>
            </div>
          </div>

          {/* AI verdict toggles */}
          <div className="space-y-2">
            <label className="flex cursor-pointer items-center gap-2 text-xs text-ink">
              <input type="checkbox"
                     data-testid="rule-require-ai-pass"
                     checked={rule.require_ai_pass}
                     onChange={(e) =>
                       setRule((p) => ({ ...p, require_ai_pass: e.target.checked }))
                     }
                     className="h-3 w-3 accent-biu-sky" />
              <span>Require AI verdict = pass</span>
            </label>
            <label className="flex cursor-pointer items-center gap-2 text-xs text-ink">
              <input type="checkbox"
                     data-testid="rule-respect-ai-fail"
                     checked={rule.respect_ai_fail}
                     onChange={(e) =>
                       setRule((p) => ({ ...p, respect_ai_fail: e.target.checked }))
                     }
                     className="h-3 w-3 accent-biu-sky" />
              <span>Skip candidates the AI flagged as wrong</span>
            </label>
          </div>

          {error && (
            <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300"
                 data-testid="authority-auto-approve-error">
              {error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between border-t border-white/10 px-4 py-3">
          <div className="text-xs muted" data-testid="authority-auto-approve-preview">
            Preview: <span className="text-ink">{previewLabel}</span>
          </div>
          <div className="flex items-center gap-2">
            <button type="button" onClick={onClose}
                    className="button-ghost h-8 px-3 text-xs" disabled={applying}>
              Cancel
            </button>
            <button type="button" onClick={handleApply}
                    data-testid="authority-auto-approve-apply"
                    className="button-primary h-8 px-3 text-xs"
                    disabled={applying || preview === 0}>
              {applying ? "Applying…" : "Apply"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
