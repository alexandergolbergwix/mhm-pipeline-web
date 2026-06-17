/**
 * AuthorityDetailDrawer — pinnable right-side drawer surfacing all signals
 * for one authority candidate:
 *
 *  1. Match info — matched name, preferred forms, IDs (Mazal/VIAF/Wikidata),
 *     KIMA geo data.
 *  2. Confidence & sources — confidence badge, pipeline reasoning, source list,
 *     guard flags with explanations.
 *  3. Dates — manuscript year / born / died, role-aware compatibility note.
 *  4. AI verdict — overall verdict pill, reasoning, model, re-verify button.
 *
 * Replaces the tab-based MatchDetailDialog modal (same content, drawer layout).
 * Pattern matches EntityDetailDrawer from the AI Extraction section.
 */

import { useEffect, useRef, useState } from "react";

import { ApiError } from "@/api/client";
import { Runs, type AuthorityMatch } from "@/api/runs";
import { AuthorityMatchEditDialog } from "@/components/AuthorityMatchEditDialog";
import { MarcRecordPopup } from "@/components/MarcRecordPopup";
import { ConfidenceBadge, VerdictBadge } from "@/components/MatchDetailDialog";
import { HistoryTimeline } from "@/components/history/HistoryTimeline";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import {Glass, GlassPill} from "@/components/glass";

export interface AuthorityDetailDrawerProps {
  match:          AuthorityMatch | null;   // null = drawer closed
  runId:          string;
  projectId:      string;
  onClose:        () => void;
  onMatchChanged: (updated: AuthorityMatch) => void;
}

export function AuthorityDetailDrawer({
  match, runId, projectId, onClose, onMatchChanged,
}: AuthorityDetailDrawerProps) {
  const [pinned, setPinned] = useState(false);
  const [busy, setBusy] = useState(false);
  const [verifyBusy, setVerifyBusy] = useState(false);
  const [verifyError, setVerifyError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [marcCn, setMarcCn] = useState<string | null>(null);
  const [historyFor, setHistoryFor] = useState<{ id: string } | null>(null);
  const drawerRef = useRef<HTMLElement>(null);

  const open = match !== null;
  useFocusTrap(open, drawerRef);

  // Esc closes (unless pinned).
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !pinned) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, pinned, onClose]);

  async function toggleApproval() {
    if (!match) return;
    setBusy(true);
    try {
      const updated = await Runs.setApproval(runId, match.id, !match.approved);
      onMatchChanged(updated);
    } catch {
      // non-fatal UI toggle
    } finally {
      setBusy(false);
    }
  }

  async function reject() {
    if (!match || match.approved === false) return;
    setBusy(true);
    try {
      const updated = await Runs.setApproval(runId, match.id, false);
      onMatchChanged(updated);
    } catch {
      // non-fatal
    } finally {
      setBusy(false);
    }
  }

  async function verifyWithAi() {
    if (!match) return;
    setVerifyBusy(true);
    setVerifyError(null);
    try {
      const r = await Runs.aiVerify(runId, match.id);
      onMatchChanged({ ...match, payload: { ...match.payload, ai_verdict: r } });
    } catch (e) {
      setVerifyError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setVerifyBusy(false);
    }
  }

  const p = (match?.payload ?? {}) as Record<string, unknown>;

  return (
    <>
      <Glass as="aside" className="flex flex-col p-0" ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="authority-detail-drawer-title"
        data-testid="authority-detail-drawer"
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
        {/* Header */}
        <header className="flex items-start justify-between gap-3 px-4 pt-4 pb-2 border-b border-white/5">
          <div className="min-w-0">
            <div className="kicker">Authority candidate</div>
            <h3 id="authority-detail-drawer-title"
                className="text-base font-semibold truncate" dir="auto">
              {match?.entity_text ?? ""}
            </h3>
            <div className="text-[11px] muted truncate">
              MS <span className="font-mono">{match?.control_number}</span>
              {match?.role && <> · <span className="kicker">{match.role}</span></>}
              {match?.entity_kind && <> · <span className="text-ink">{match.entity_kind}</span></>}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {match && (
              <button
                type="button"
                data-testid={`history-button-${match.id}`}
                onClick={() => setHistoryFor({ id: String(match.id) })}
                aria-label="View edit history"
                title="View edit history"
                className="button-ghost h-7 px-2 text-xs"
              >📜</button>
            )}
            <label className="muted flex items-center gap-1 cursor-pointer select-none text-[11px]"
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

        {/* Sticky quick-action row */}
        {match && (
          <div className="sticky top-0 z-10 flex items-center gap-2 px-4 py-2 border-b border-white/5 bg-[rgba(16,24,36,0.6)] flex-wrap">
            <button type="button" disabled={busy}
                    onClick={toggleApproval}
                    data-testid="drawer-approve"
                    className={`button-${match.approved ? "ghost" : "primary"} h-7 px-3 text-xs`}>
              {match.approved ? "✓ Approved" : "Approve"}
            </button>
            <button type="button" disabled={busy || !match.approved}
                    onClick={reject}
                    data-testid="drawer-reject"
                    className="button-ghost h-7 px-3 text-xs">
              Reject
            </button>
            <button type="button"
                    onClick={() => setEditing(true)}
                    data-testid="drawer-edit"
                    className="button-ghost h-7 px-3 text-xs">
              Edit…
            </button>
            <button type="button"
                    onClick={() => setMarcCn(match.control_number)}
                    className="button-ghost h-7 px-3 text-xs">
              View MARC
            </button>
            <button type="button" disabled={verifyBusy}
                    onClick={verifyWithAi}
                    data-testid="drawer-verify-ai"
                    className="button-ghost h-7 px-3 text-xs ml-auto">
              {verifyBusy ? "Asking…" : (p.ai_verdict ? "Re-verify with AI" : "Verify with AI")}
            </button>
          </div>
        )}

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3 text-xs">
          {match && (
            <>
              {verifyError && (
                <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                  {verifyError}
                </div>
              )}
              <MatchCard match={match} payload={p}
                         onViewMarc={() => setMarcCn(match.control_number)} />
              <ConfidenceCard match={match} payload={p} />
              <DatesCard match={match} payload={p} />
              <AiVerdictCard payload={p} />
            </>
          )}
        </div>
      </Glass>

      {/* Edit dialog — z-[60] so it floats above the drawer */}
      {editing && match && (
        <AuthorityMatchEditDialog
          runId={runId}
          match={match}
          onClose={() => setEditing(false)}
          onSaved={(next) => { onMatchChanged(next); setEditing(false); }}
        />
      )}

      {/* MARC popup */}
      {marcCn && (
        <MarcRecordPopup runId={runId} controlNumber={marcCn}
                         onClose={() => setMarcCn(null)} />
      )}

      {/* Nested history drawer */}
      {historyFor && (
        <Glass as="aside" variant="drawer" className="fixed right-0 top-0 h-full w-[460px] shadow-2xl z-50 overflow-auto" data-testid="authority-detail-history-drawer">
          <HistoryTimeline
            projectId={projectId}
            entityType="authority_match"
            entityId={historyFor.id}
            onClose={() => setHistoryFor(null)}
          />
        </Glass>
      )}
    </>
  );
}


// ── Cards ─────────────────────────────────────────────────────────────────


function MatchCard({
  match, payload, onViewMarc,
}: {
  match:      AuthorityMatch;
  payload:    Record<string, unknown>;
  onViewMarc: () => void;
}) {
  const prefLat = strOr(payload.preferred_name_lat, "");
  const prefHeb = strOr(payload.preferred_name_heb, "");

  return (
    <Glass as="section" variant="compact" className="p-3 space-y-2" data-testid="drawer-card-match">
      <div className="kicker">🔍 Match</div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-2">
        <LabeledValue label="Matched authority" value={match.matched_name || "—"} />
        {prefLat && <LabeledValue label="Preferred (Latin)" value={prefLat} />}
        {prefHeb && <LabeledValue label="Preferred (Hebrew)" value={prefHeb} dir="rtl" />}
        {match.mazal_id && (
          <LabeledValue label="Mazal / NLI ID" value={match.mazal_id} mono
            href={`https://www.nli.org.il/he/authorities/${encodeURIComponent(match.mazal_id)}`} />
        )}
        {match.viaf_id && (
          <LabeledValue label="VIAF" value={match.viaf_id} mono
            href={`https://viaf.org/viaf/${match.viaf_id}`} />
        )}
        {match.wikidata_qid && (
          <LabeledValue label="Wikidata" value={match.wikidata_qid} mono
            href={`https://www.wikidata.org/wiki/${match.wikidata_qid}`} />
        )}
      </div>
      <KimaGeoInline payload={payload} />
      <button type="button" onClick={onViewMarc}
              className="text-biu-sky text-[11px] hover:underline">
        View MARC record ↗
      </button>
    </Glass>
  );
}


function ConfidenceCard({
  match, payload,
}: {
  match:   AuthorityMatch;
  payload: Record<string, unknown>;
}) {
  const sources   = (payload.sources as string[] | undefined) ?? [];
  const guards    = (payload.guard_flags as string[] | undefined) ?? [];
  const count     = Number(payload.source_count ?? sources.length);
  const reasoning = strOr(payload.reasoning, "");

  return (
    <Glass as="section" variant="compact" className="p-3 space-y-2" data-testid="drawer-card-confidence">
      <div className="kicker">📊 Confidence &amp; sources</div>
      <div className="flex items-center gap-3">
        <ConfidenceBadge confidence={match.confidence} />
        <span className="muted text-[11px]">
          {count} source{count !== 1 ? "s" : ""}
          {count >= 2 && <span className="text-biu-sky ml-1">✓ cross-source</span>}
        </span>
      </div>
      {reasoning && (
        <blockquote className="border-l-2 border-biu-sky/40 pl-2 text-[11px] text-ink/90 leading-snug">
          {reasoning}
        </blockquote>
      )}
      {sources.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {sources.map((s) => (
            <GlassPill className="px-2 py-0.5 text-[10px] uppercase tracking-wider text-biu-sky" key={s}>
              {s}
            </GlassPill>
          ))}
        </div>
      )}
      {guards.length > 0 && (
        <div>
          <div className="muted text-[10px] uppercase mb-1">Guards fired</div>
          <ul className="space-y-0.5">
            {guards.map((g) => (
              <li key={g} className="text-[11px]">
                <span className="text-red-300 mr-1">⚠</span>
                <span className="font-mono">{g}</span>
                {guardExplain(g) && <span className="muted ml-2">{guardExplain(g)}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Glass>
  );
}


function DatesCard({
  match, payload,
}: {
  match:   AuthorityMatch;
  payload: Record<string, unknown>;
}) {
  const msYear   = payload.ms_year as number | null | undefined;
  const birth    = payload.birth_year as number | null | undefined;
  const death    = payload.death_year as number | null | undefined;
  const role     = (payload.role_kind as string | undefined) || "other";
  const conflict = (payload.guard_flags as string[] | undefined)?.includes("date_conflict");
  const dateGuardRan = (birth || death) && msYear;
  const hasIds   = !!(match.viaf_id || match.wikidata_qid || match.mazal_id);

  if (!msYear && !birth && !death) return null;

  return (
    <Glass as="section" variant="compact" className="p-3 space-y-2" data-testid="drawer-card-dates">
      <div className="kicker">📅 Dates</div>
      <div className="grid grid-cols-3 gap-2 text-center">
        <GlassPill as="div" className="py-2">
          <div className="muted text-[10px] uppercase">Manuscript</div>
          <div className="text-sm text-ink">{msYear ?? "—"}</div>
        </GlassPill>
        <GlassPill as="div" className="py-2">
          <div className="muted text-[10px] uppercase">Born</div>
          <div className="text-sm text-ink">{birth ?? "—"}</div>
        </GlassPill>
        <GlassPill as="div" className="py-2">
          <div className="muted text-[10px] uppercase">Died</div>
          <div className="text-sm text-ink">{death ?? "—"}</div>
        </GlassPill>
      </div>
      <p className="text-[11px]">
        Role kind: <b className="text-ink">{role}</b>.{" "}
        {role === "production" && "Death year checked — scribe can't post-date their death."}
        {role === "authorship" && "Birth year checked — author must predate the manuscript."}
        {role === "subject" && "Death check is silent (manuscripts can be about someone long dead)."}
        {role === "other" && "Lenient — only birth is checked."}
      </p>
      <p className="text-[11px]">
        {conflict
          ? <span className="text-red-300">⚠ Date guard fired — confidence may have been downgraded.</span>
          : dateGuardRan
            ? <span className="text-biu-sky">✓ Dates compatible with the role.</span>
            : hasIds
              ? <span className="muted">
                  — Dates not populated. Click <b className="text-ink">Backfill dates</b> to pull years from stored IDs.
                </span>
              : <span className="muted">— Date guard skipped: no birth/death years available.</span>}
      </p>
    </Glass>
  );
}


function AiVerdictCard({ payload }: { payload: Record<string, unknown> }) {
  const verdict = payload.ai_verdict as null | {
    overall: "full" | "partial" | "fail" | "abstain";
    reasoning: string;
    model: string;
    judged_at: string;
    fallback?: boolean;
  };

  if (!verdict) {
    return (
      <Glass as="section" variant="compact" className="p-3" data-testid="drawer-card-ai-verdict">
        <div className="kicker">🤖 AI verification</div>
        <div className="muted text-[11px] mt-1">
          Not yet judged. Use the <strong className="text-ink">Verify with AI</strong> button above.
        </div>
      </Glass>
    );
  }

  return (
    <Glass as="section" variant="compact" className="p-3 space-y-2" data-testid="drawer-card-ai-verdict">
      <div className="kicker">🤖 AI verification</div>
      <div className="flex items-center gap-3 flex-wrap">
        <VerdictBadge overall={verdict.overall} />
        <span className="kicker text-[10px]">
          {verdict.model}
          {verdict.fallback && <span className="ml-2 text-yellow-300">heuristic fallback</span>}
        </span>
      </div>
      {verdict.reasoning && (
        <blockquote className="border-l-2 border-biu-sky/40 pl-2 text-[11px] text-ink/90 leading-snug">
          {verdict.reasoning}
        </blockquote>
      )}
      <div className="muted text-[10px]">
        Judged: {new Date(verdict.judged_at).toLocaleString()}
      </div>
    </Glass>
  );
}


// ── KIMA geo inline (renders only for place matches) ─────────────────────


function KimaGeoInline({ payload }: { payload: Record<string, unknown> }) {
  const lat      = payload.kima_lat as number | string | undefined;
  const lon      = payload.kima_lon as number | string | undefined;
  const heb      = strOr(payload.kima_heb, "");
  const rom      = strOr(payload.kima_rom, "");
  const geonames = strOr(payload.kima_geonames, "");
  if (lat == null && lon == null && !heb && !rom) return null;
  const latN = lat != null ? Number(lat) : NaN;
  const lonN = lon != null ? Number(lon) : NaN;
  const osmHref = !Number.isNaN(latN) && !Number.isNaN(lonN)
    ? `https://www.openstreetmap.org/?mlat=${latN}&mlon=${lonN}#map=12/${latN}/${lonN}`
    : undefined;
  return (
    <GlassPill as="div" className="p-2 space-y-0.5 text-[11px]">
      <div className="muted text-[10px] uppercase">KIMA location</div>
      {heb && <div><span className="muted">He: </span>{heb}</div>}
      {rom && <div><span className="muted">Rom: </span>{rom}</div>}
      {(lat != null || lon != null) && (
        <div className="font-mono">
          {lat ?? "—"}, {lon ?? "—"}
          {osmHref && (
            <a href={osmHref} target="_blank" rel="noreferrer"
               className="ml-2 text-biu-sky hover:underline">OSM ↗</a>
          )}
        </div>
      )}
      {geonames && <div className="muted">GeoNames: {geonames}</div>}
    </GlassPill>
  );
}


// ── Tiny presentational helpers ──────────────────────────────────────────


function LabeledValue({
  label, value, mono, href, dir,
}: {
  label:  string;
  value:  string;
  mono?:  boolean;
  href?:  string;
  dir?:   string;
}) {
  return (
    <div>
      <div className="muted text-[10px] uppercase">{label}</div>
      <div className={`text-[11px] text-ink break-all ${mono ? "font-mono" : ""}`} dir={dir}>
        {href
          ? <a href={href} target="_blank" rel="noreferrer"
               className="text-biu-sky hover:underline">{value}</a>
          : value}
      </div>
    </div>
  );
}


function strOr(v: unknown, fallback: string): string {
  return typeof v === "string" && v.length > 0 ? v : fallback;
}


const _GUARD_NOTES: Record<string, string> = {
  date_conflict:      "Manuscript date is incompatible with the candidate's lifespan + role.",
  name_drift:         "Preferred form drifts from the MARC heading.",
  weaker_alternative: "Secondary candidate offered alongside a stronger primary.",
};
function guardExplain(g: string): string {
  return _GUARD_NOTES[g] ?? "";
}
