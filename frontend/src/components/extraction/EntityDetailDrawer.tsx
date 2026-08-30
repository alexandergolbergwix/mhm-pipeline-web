/**
 * EntityDetailDrawer — right-side drawer that surfaces EVERY signal
 * a curator needs to approve/reject one NER extraction:
 *
 *  1. Confidence breakdown — keyword classifier (0.60 / 0.85
 *     buckets, Rule 41) + BIO softmax probability, each with a
 *     short explanation of what the number means.
 *  2. MARC grounding — exists_in status (grounded / wrong_field /
 *     novel / unknown), the fields the classifier checked, the
 *     fields where the candidate actually matched (with their MARC
 *     values shown side-by-side).
 *  3. AI verification verdict — overall + sub-judgements
 *     (name_ok / type_ok / role_ok) + the full reasoning the
 *     LLM produced.
 *  4. Full MARC record — every populated key, ordered by a
 *     researcher-friendly weight (title → contributors → subjects
 *     → places → provenance → physical → dates → notes).
 *     Entity text is highlighted in yellow everywhere it appears,
 *     other entities in the same record in blue (so the curator
 *     sees the full population at a glance).
 *  5. Quick action row — Approve / Reject / Edit (closes drawer +
 *     opens the edit modal).
 *
 * Replaces the older MarcSourceDrawer; the EntityTable's row click,
 * AI verdict pill, and 👁 button all open this single drawer.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { MarcSourceApi, type MarcSource } from "@/api/marcSource";
import {
  ExtractionApprovals,
  type Entity,
} from "@/api/extractionApprovals";
import { recheckEntity } from "@/api/nerVerify";
import {emitEntitiesRefreshed} from "@/cache/extractionCache";
import {
  ENTITY_EXPECTED_FIELDS, MARC_FIELD_LABELS,
  orderedMarcKeys, stringifyMarcValue,
} from "@/components/extraction/marc-field-labels";
import {HistoryTimeline} from "@/components/history/HistoryTimeline";
import {useFocusTrap} from "@/hooks/useFocusTrap";
import {useGlassOverlayLifecycle} from "@/hooks/useGlassOverlayLifecycle";
import {useAuth} from "@/stores/auth";
import { langOf } from "@/utils/hebrew";
import {Glass} from "@/components/glass";


export interface EntityDetailDrawerProps {
  runId: string;
  projectId: string;
  /** The entity currently being inspected. ``null`` ⇒ drawer closed. */
  entity: Entity | null;
  onClose: () => void;
  /** Fired when the curator saves a change (approve / reject / edit).
   *  Parent should call ``useApprovalStore().refresh()`` to repaint. */
  onEntityChanged?: (updated: Entity) => void;
  /** Optional callback to open the full edit modal (text/type/role
   *  side-by-side editor). Passes loaded MARC when available. */
  onOpenEdit?: (entity: Entity, marcSource?: MarcSource | null) => void;
  /** Trigger AI verification scoped to this single entity. */
  onVerifyEntity?: (entity: Entity) => void;
}


export function EntityDetailDrawer(props: EntityDetailDrawerProps) {
  const { runId, projectId, entity, onClose, onEntityChanged, onOpenEdit, onVerifyEntity } = props;

  const userId = useAuth((s) => s.user?.id) ?? "";

  const [marc, setMarc] = useState<MarcSource | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pinned, setPinned] = useState(false);
  const [busy, setBusy] = useState(false);
  const [rechecking, setRechecking] = useState(false);
  const [historyFor, setHistoryFor] = useState<{ id: string } | null>(null);
  const drawerRef = useRef<HTMLElement>(null);

  const open = entity !== null;
  const cn = entity?.control_number ?? null;

  useFocusTrap(open, drawerRef);
  useGlassOverlayLifecycle(open);
  useGlassOverlayLifecycle(historyFor !== null);

  // Load the MARC record + sibling entities when entity changes.
  useEffect(() => {
    if (!cn) { setMarc(null); setError(null); return; }
    let cancelled = false;
    setLoading(true); setError(null);
    MarcSourceApi.get(runId, cn)
      .then((m) => { if (!cancelled) setMarc(m); })
      .catch((e: Error) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [runId, cn]);

  // Esc closes (unless pinned).
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !pinned) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, pinned, onClose]);

  async function patchEntity(patch: Record<string, unknown>): Promise<void> {
    if (!entity) return;
    setBusy(true);
    try {
      const updated = await ExtractionApprovals.patch(runId, entity.id, patch);
      emitEntitiesRefreshed(userId, runId);
      onEntityChanged?.(updated);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function applyAutoFix(fixText: string): Promise<void> {
    if (!entity) return;
    setBusy(true);
    setRechecking(true);
    let updated: Entity = entity;
    try {
      // Patch only the text override — deliberately does NOT set approved.
      // The curator sees the updated text and can then approve if they agree.
      updated = await ExtractionApprovals.patch(runId, entity.id, { text: fixText });
      emitEntitiesRefreshed(userId, runId);
      // Re-run the AI check for this one entity against the corrected text so
      // the verdict reflects the fix. A failed re-check is non-fatal.
      await recheckEntity(runId, entity.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      // Single refetch after both steps so the store doesn't drop one of two
      // racing refreshes — picks up the new text AND the updated verdict.
      onEntityChanged?.(updated);
      setRechecking(false);
      setBusy(false);
    }
  }

  // Other entities in the same record — used for secondary (blue)
  // highlights in the MARC field renderer.
  const siblingTexts = useMemo<string[]>(() => {
    if (!marc || !entity) return [];
    const out: string[] = [];
    for (const e of marc.entities) {
      if (e.id === entity.id) continue;
      const t = (e.text ?? "").trim();
      if (t.length >= 2) out.push(t);
    }
    return out;
  }, [marc, entity]);

  return (
    <Glass as="aside" variant="drawer" refraction={false} className="flex flex-col p-0" ref={drawerRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby="entity-detail-drawer-title"
      data-testid="entity-detail-drawer"
      style={{
        position:      "fixed",
        top:           0,
        right:         0,
        bottom:        0,
        width:         "min(640px, 95vw)",
        zIndex:        40,
        transform:     open ? "translateX(0)" : "translateX(110%)",
        transition:    "transform 220ms ease-out",
        pointerEvents: open ? "auto" : "none",
      }}>
      {/* Header — entity identity + pin/close */}
      <header className="flex items-start justify-between gap-3 px-4 pt-4 pb-2 border-b border-white/5">
        <div className="min-w-0">
          <div className="kicker">Entity review</div>
          <h3 id="entity-detail-drawer-title"
              className="text-base font-semibold truncate" dir="auto"
              lang={langOf(entity?.text)}
              title={entity?.text ?? ""}>
            {entity?.text ?? ""}
          </h3>
          <div className="text-[11px] muted truncate">
            MS <span className="font-mono">{entity?.control_number}</span> ·
            <span className="ml-1 uppercase">{entity?.source}</span>
            {entity?.type && <span className="ml-1 text-ink">{entity.type}</span>}
            {entity?.role && <span className="ml-1 text-biu-sky">{entity.role}</span>}
          </div>
        </div>
        <div className="flex items-center gap-2 text-[11px]">
          {entity && projectId ? (
            <button
              type="button"
              data-testid={`history-button-${entity.id}`}
              onClick={() => setHistoryFor({id: entity.approval_row_id ?? String(entity.id)})}
              aria-label="View edit history"
              title="View edit history"
              className="button-ghost h-7 px-2 text-xs"
            >📜</button>
          ) : null}
          <label className="muted flex items-center gap-1 cursor-pointer select-none"
                 title="Keep this drawer open while browsing the table.">
            <input type="checkbox" checked={pinned}
                   onChange={(e) => setPinned(e.target.checked)} />
            Pin
          </label>
          <button onClick={onClose}
                  className="button-ghost !py-0.5 !px-2 text-xs"
                  title="Close (Esc)">✕</button>
        </div>
      </header>

      {/* Quick action row — sticky at top so curator can decide
          without scrolling. */}
      {entity && (
        <div className="sticky top-0 z-10 flex items-center gap-2 px-4 py-2 border-b border-white/5 bg-[rgba(16,24,36,0.6)]">
          <button type="button"
                  disabled={busy}
                  onClick={() => patchEntity({ approved: !entity.approved })}
                  data-testid="detail-approve"
                  className={`button-${entity.approved ? "ghost" : "primary"} h-7 px-3 text-xs`}>
            {entity.approved ? "✓ Approved" : "Approve"}
          </button>
          <button type="button"
                  disabled={busy}
                  onClick={() => patchEntity({ approved: false, rejected: true })}
                  data-testid="detail-reject"
                  className="button-ghost h-7 px-3 text-xs">
            Reject
          </button>
          {onOpenEdit && (
            <button type="button"
                    onClick={() => onOpenEdit(entity, marc)}
                    data-testid="detail-edit"
                    className="button-ghost h-7 px-3 text-xs">
              Edit text/type/role…
            </button>
          )}
          {(() => {
            const fix = entity.ai_verdict?.suggested_fix;
            const effectiveText = (entity.effective_text ?? entity.text ?? "").trim();
            if (
              fix != null &&
              fix.confidence === "high" &&
              entity.source !== "genre_ml" &&
              fix.text.trim() !== effectiveText
            ) {
              return (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => applyAutoFix(fix.text)}
                  data-testid="detail-autofix-btn"
                  title={fix.reasoning ?? `Apply AI-suggested correction \u2192 ${fix.text}`}
                  className="button-ghost h-7 px-3 text-xs text-warn hover:opacity-90"
                >
                  ✨ Auto-fix
                </button>
              );
            }
            return null;
          })()}
          {rechecking && (
            <span
              data-testid="detail-rechecking"
              className="inline-flex items-center h-7 px-2 text-xs text-warn opacity-80 animate-pulse"
            >
              ⏳ re-checking…
            </span>
          )}
          {onVerifyEntity && (
            <button type="button"
                    disabled={busy}
                    onClick={() => onVerifyEntity(entity)}
                    data-testid="detail-verify-ai"
                    className="button-ghost h-7 px-3 text-xs">
              {entity.ai_verdict ? "Re-verify with AI" : "Verify with AI"}
            </button>
          )}
          <span className="muted text-[11px] ml-auto">
            {entity.start !== null && entity.end !== null
              ? `chars ${entity.start}–${entity.end}`
              : ""}
          </span>
        </div>
      )}

      {/* Scroll body */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4 text-xs">
        {entity && (
          <>
            <ConfidenceCard entity={entity} />
            <GroundingCard entity={entity} marc={marc} />
            <AiVerdictCard entity={entity} />
            <MarcRecordCard marc={marc} loading={loading} error={error}
                            primaryHighlight={entity.text} secondaryHighlights={siblingTexts} />
          </>
        )}
      </div>
      {historyFor ? (
        <Glass as="aside" variant="drawer" refraction={false} className="fixed right-0 top-0 h-full w-[460px] shadow-2xl z-50 overflow-auto" data-testid="entity-detail-history-drawer">
          <HistoryTimeline
            projectId={projectId}
            entityType="extraction_entity"
            entityId={historyFor.id}
            onClose={() => setHistoryFor(null)}
          />
        </Glass>
      ) : null}
    </Glass>
  );
}


// ── Confidence card ───────────────────────────────────────────────────


function ConfidenceCard({ entity }: { entity: Entity }) {
  const keyword = entity.confidence;
  const model = entity.model_confidence;
  const bucket = (n: number | null): string => {
    if (n === null) return "—";
    if (n >= 0.85) return "high";
    if (n >= 0.60) return "medium";
    return "low";
  };
  return (
    <Glass as="section" variant="compact" refraction={false} className="p-3 space-y-2" data-testid="card-confidence">
      <div className="kicker">📊 Confidence</div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="muted text-[10px] uppercase">Keyword classifier</div>
          <div className="text-sm">
            <span className="font-mono">{keyword?.toFixed(2) ?? "—"}</span>{" "}
            <span className="muted">({bucket(keyword)})</span>
          </div>
          <p className="muted text-[10px] mt-0.5 leading-snug">
            Buckets 0.60 / 0.85 from the role-keyword heuristic
            (Rule 41). Drives Stage-3 date guards.
          </p>
        </div>
        <div>
          <div className="muted text-[10px] uppercase">Model softmax</div>
          <div className="text-sm">
            <span className="font-mono">{model?.toFixed(2) ?? "—"}</span>{" "}
            <span className="muted">({bucket(model)})</span>
          </div>
          <p className="muted text-[10px] mt-0.5 leading-snug">
            BIO softmax probability averaged over the entity's tokens.
            Pure model signal, no rule layer.
          </p>
        </div>
      </div>
    </Glass>
  );
}


// ── MARC grounding card ──────────────────────────────────────────────


function GroundingCard({
  entity, marc,
}: {
  entity: Entity;
  marc: MarcSource | null;
}) {
  const ex = entity.exists_in;
  const expected = ENTITY_EXPECTED_FIELDS[entity.type] ?? [];

  const statusLabel: Record<string, { label: string; bg: string; fg: string; explain: string }> = {
    grounded:    { label: "Grounded", bg: "rgba(120,200,140,0.18)", fg: "#7adf95",
                   explain: "The extracted text appears in the MARC field(s) the NER's type expects. High-confidence approval candidate." },
    wrong_field: { label: "Wrong field", bg: "rgba(253,224,71,0.18)", fg: "#fde047",
                   explain: "The text appears in MARC but in a field other than the one this entity type usually maps to. Worth double-checking." },
    novel:       { label: "Novel", bg: "rgba(127,196,255,0.18)", fg: "#77cce5",
                   explain: "Not present in any catalogued MARC field. NER surfaced new information from free-text notes — review carefully." },
    unknown:     { label: "—", bg: "transparent", fg: "var(--muted)",
                   explain: "No MARC record indexed for this control number." },
  };
  const status = statusLabel[ex?.status ?? "unknown"] ?? statusLabel.unknown;

  return (
    <Glass as="section" variant="compact" refraction={false} className="p-3 space-y-2" data-testid="card-grounding">
      <div className="kicker">🔍 MARC grounding</div>
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center rounded-full text-[10px] py-0.5 px-2"
              style={{ background: status.bg, color: status.fg, border: `1px solid ${status.fg}33` }}>
          {status.label}
        </span>
        <span className="muted text-[11px] leading-snug">{status.explain}</span>
      </div>
      <div className="grid grid-cols-2 gap-3 mt-1">
        <div>
          <div className="muted text-[10px] uppercase mb-1">Expected fields</div>
          {expected.length === 0 ? (
            <div className="muted text-[11px] italic">(none registered for type {entity.type})</div>
          ) : (
            <ul className="space-y-0.5 text-[11px]">
              {expected.map((k) => {
                const lbl = MARC_FIELD_LABELS[k];
                const hit = ex?.fields?.includes(k) ?? false;
                return (
                  <li key={k} className={hit ? "text-ink" : "muted"}>
                    {hit ? "✓ " : "· "}
                    {lbl?.label ?? k} <span className="muted">({lbl?.tag ?? "—"})</span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        <div>
          <div className="muted text-[10px] uppercase mb-1">Matched in</div>
          {(ex?.fields?.length ?? 0) === 0 ? (
            <div className="muted text-[11px] italic">(no MARC fields matched)</div>
          ) : (
            <ul className="space-y-1 text-[11px]">
              {ex!.fields.map((k) => {
                const lbl = MARC_FIELD_LABELS[k];
                const raw = marc?.marc?.[k];
                const valueStr = stringifyMarcValue(raw);
                return (
                  <li key={k}>
                    <div className="text-biu-sky">
                      {lbl?.label ?? k} <span className="muted">({lbl?.tag ?? "—"})</span>
                    </div>
                    <div className="text-ink/90 line-clamp-2 break-words" dir="auto"
                         lang={langOf(valueStr)}>
                      {valueStr}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
      {ex?.note && (
        <p className="muted text-[11px] mt-1 leading-snug">{ex.note}</p>
      )}
    </Glass>
  );
}


// ── AI verdict card ──────────────────────────────────────────────────


function AiVerdictCard({ entity }: { entity: Entity }) {
  const v = entity.ai_verdict;
  if (!v) {
    return (
      <Glass as="section" variant="compact" refraction={false} className="p-3" data-testid="card-ai-verdict">
        <div className="kicker">🤖 AI verification</div>
        <div className="muted text-[11px] mt-1">
          Not yet judged. Use the <strong className="text-ink">Verify with AI</strong> button above to score this entity.
        </div>
      </Glass>
    );
  }
  const overall = String(v.overall ?? "").toLowerCase();
  const tone: Record<string, { label: string; bg: string; fg: string }> = {
    pass:                { label: "Looks right",  bg: "rgba(120,200,140,0.18)", fg: "#7adf95" },
    full:                { label: "Looks right",  bg: "rgba(120,200,140,0.18)", fg: "#7adf95" },
    partial:             { label: "Partly",       bg: "rgba(253,224,71,0.18)",  fg: "#fde047" },
    fail:                { label: "Wrong",        bg: "rgba(248,113,113,0.18)", fg: "#fca5a5" },
    abstain:             { label: "Unsure",       bg: "rgba(255,255,255,0.06)", fg: "var(--muted)" },
    verification_failed: { label: "Provider error", bg: "rgba(253,186,116,0.18)", fg: "#fb923c" },
  };
  const t = tone[overall] ?? tone.abstain;
  const sub = (label: string, val: unknown) => {
    const b = typeof val === "boolean" ? val : null;
    const glyph = b === true ? "✓" : b === false ? "✗" : "—";
    const color = b === true ? "text-success" : b === false ? "text-danger" : "muted";
    return <span className={color}>{glyph} {label}</span>;
  };

  // v2 suggested_fix preview — show current vs proposed text so the
  // curator can see exactly what will change before clicking Auto-fix.
  const fix = v.suggested_fix;
  const effectiveText = (entity.effective_text ?? entity.text ?? "").trim();
  const showFix =
    fix != null &&
    fix.confidence === "high" &&
    entity.source !== "genre_ml" &&
    fix.text.trim() !== effectiveText;

  return (
    <Glass as="section" variant="compact" refraction={false} className="p-3 space-y-2" data-testid="card-ai-verdict">
      <div className="kicker">🤖 AI verification</div>
      <div className="flex items-center gap-3">
        <span className="inline-flex items-center rounded-full text-[10px] py-0.5 px-2"
              style={{ background: t.bg, color: t.fg, border: `1px solid ${t.fg}33` }}>
          {t.label}
        </span>
        <div className="text-[11px] flex gap-3 muted">
          {sub("name", v.name_ok)}
          {sub("type", v.type_ok)}
          {sub("role", v.role_ok)}
        </div>
      </div>
      {v.reasoning && (
        <blockquote className="border-l-2 border-biu-sky/40 pl-2 text-[11px] text-ink/90 leading-snug">
          {v.reasoning}
        </blockquote>
      )}
      {showFix && (
        <div className="rounded-md border border-amber-300/30 bg-amber-300/5 p-2 space-y-1"
             data-testid="autofix-preview">
          <div className="text-[10px] uppercase tracking-wide text-warn opacity-80">✨ AI suggests a text fix</div>
          <div className="grid grid-cols-[60px_1fr] gap-x-2 text-[11px]">
            <span className="muted text-right">Current:</span>
            <span dir="auto" data-testid="autofix-current-text"
                  className="text-ink/70 line-through">{effectiveText}</span>
            <span className="muted text-right">Proposed:</span>
            <span dir="auto" data-testid="autofix-proposed-text"
                  className="text-warn font-medium">{fix!.text}</span>
          </div>
          {fix!.reasoning && (
            <p className="text-[10px] muted leading-snug">{fix!.reasoning}</p>
          )}
        </div>
      )}
      <div className="muted text-[10px]">
        {v.model && <span>model: {v.model}</span>}
        {v.judged_at && <span className="ml-2">judged: {new Date(v.judged_at).toLocaleString()}</span>}
        {v.evaluator && <span className="ml-2">evaluator: {v.evaluator}</span>}
      </div>
    </Glass>
  );
}


// ── MARC record card ─────────────────────────────────────────────────


function MarcRecordCard({
  marc, loading, error, primaryHighlight, secondaryHighlights,
}: {
  marc:                MarcSource | null;
  loading:             boolean;
  error:               string | null;
  primaryHighlight:    string;
  secondaryHighlights: string[];
}) {
  if (loading) return <Glass as="section" variant="compact" refraction={false} className="p-3 muted text-[11px] italic">Loading MARC record…</Glass>;
  if (error) return <Glass as="section" variant="compact" refraction={false} className="p-3 text-danger text-[11px]">MARC source failed: {error}</Glass>;
  if (!marc) return null;
  const keys = orderedMarcKeys(marc.marc);
  if (keys.length === 0) return <Glass as="section" variant="compact" refraction={false} className="p-3 muted text-[11px] italic">No MARC fields available.</Glass>;
  return (
    <Glass as="section" variant="compact" refraction={false} className="p-3 space-y-2" data-testid="card-marc-record">
      <div className="kicker">📜 MARC record ({keys.length} fields)</div>
      <div className="space-y-2">
        {keys.map((k) => {
          const lbl = MARC_FIELD_LABELS[k];
          const value = stringifyMarcValue(marc.marc[k]);
          if (!value) return null;
          return (
            <div key={k} className="grid grid-cols-[140px_1fr] gap-2 items-baseline">
              <div className="text-right text-[10px]">
                <div className="text-ink">{lbl?.label ?? k}</div>
                <div className="muted font-mono">{lbl?.tag ?? ""}</div>
              </div>
              <div className="text-[11px] text-ink/90 break-words leading-relaxed" dir="auto"
                   lang={langOf(value)}>
                <Highlighted value={value}
                             primary={primaryHighlight}
                             secondaries={secondaryHighlights} />
              </div>
            </div>
          );
        })}
      </div>
    </Glass>
  );
}


// ── Pure highlight renderer ──────────────────────────────────────────


function Highlighted({
  value, primary, secondaries,
}: {
  value:       string;
  primary:     string;
  secondaries: string[];
}) {
  const parts = useMemo(() => splitHighlights(value, primary, secondaries), [value, primary, secondaries]);
  return (
    <>
      {parts.map((p, i) => {
        if (p.kind === "plain") return <span key={i}>{p.text}</span>;
        if (p.kind === "primary") return (
          <mark key={i} style={{ background: "rgba(253,224,71,0.42)", color: "var(--ink)",
                                 padding: "0 2px", borderRadius: 3 }}>{p.text}</mark>
        );
        return (
          <mark key={i} style={{ background: "rgba(127,196,255,0.28)", color: "var(--ink)",
                                 padding: "0 2px", borderRadius: 3 }}>{p.text}</mark>
        );
      })}
    </>
  );
}


type Part =
  | { kind: "plain";     text: string }
  | { kind: "primary";   text: string }
  | { kind: "secondary"; text: string };


function splitHighlights(value: string, primary: string, secondaries: string[]): Part[] {
  if (!value) return [];
  type Range = { start: number; end: number; kind: "primary" | "secondary" };
  const ranges: Range[] = [];
  const lcVal = value.toLowerCase();
  function push(needle: string, kind: Range["kind"]) {
    const n = needle.trim();
    if (n.length < 2) return;
    const lcN = n.toLowerCase();
    let from = 0;
    while (true) {
      const idx = lcVal.indexOf(lcN, from);
      if (idx < 0) break;
      ranges.push({ start: idx, end: idx + n.length, kind });
      from = idx + n.length;
    }
  }
  if (primary) push(primary, "primary");
  for (const s of secondaries) push(s, "secondary");
  if (ranges.length === 0) return [{ kind: "plain", text: value }];

  ranges.sort((a, b) => a.start - b.start || (a.kind === "primary" ? -1 : 1));
  const chosen: Range[] = [];
  for (const r of ranges) {
    const last = chosen[chosen.length - 1];
    if (!last || r.start >= last.end) { chosen.push(r); continue; }
    if (r.kind === "primary" && last.kind !== "primary") chosen[chosen.length - 1] = r;
  }
  const out: Part[] = [];
  let cursor = 0;
  for (const r of chosen) {
    if (r.start > cursor) out.push({ kind: "plain", text: value.slice(cursor, r.start) });
    out.push({ kind: r.kind, text: value.slice(r.start, r.end) });
    cursor = r.end;
  }
  if (cursor < value.length) out.push({ kind: "plain", text: value.slice(cursor) });
  return out;
}
