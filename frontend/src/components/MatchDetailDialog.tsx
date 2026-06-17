/**
 * Rich match-detail dialog — Overview / Why / Dates & guards /
 * AI verification / Sources & cluster — fed by the matcher's full
 * marc_authority_matches payload.
 */

import {useState} from "react";

import {Runs, type AuthorityMatch} from "@/api/runs";
import {ApiError} from "@/api/client";
import {AuthorityMatchEditDialog} from "@/components/AuthorityMatchEditDialog";
import {MarcRecordPopup} from "@/components/MarcRecordPopup";
import {Glass, GlassPill} from "@/components/glass";

interface Props {
  runId: string;
  match: AuthorityMatch;
  onClose: () => void;
  onPatched: (next: AuthorityMatch) => void;
}

type Tab = "overview" | "why" | "dates" | "ai" | "sources";


export function MatchDetailDialog({ runId, match, onClose, onPatched }: Props) {
  const [tab, setTab] = useState<Tab>("overview");
  const [marcCn, setMarcCn] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const p = (match.payload ?? {}) as Record<string, unknown>;

  return (
    <div className="fixed inset-0 z-50 grid place-items-center p-4 bg-black/60"
         onClick={onClose}>
      <Glass className="w-full max-w-4xl max-h-[88vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>

        <header className="px-6 py-4 flex items-start justify-between gap-4 border-b border-white/10">
          <div className="min-w-0">
            <div className="kicker">
              {strOr(p.matcher, "match")} · record{" "}
              <button onClick={() => setMarcCn(match.control_number)}
                      className="text-biu-sky hover:underline font-mono normal-case tracking-normal">
                {match.control_number}
              </button>
            </div>
            <h3 className="text-xl font-semibold truncate">{match.entity_text}</h3>
            <p className="muted text-sm mt-1">
              {match.role && <><span className="kicker">{match.role}</span> · </>}
              matched to <span className="text-ink">{match.matched_name || "—"}</span>
            </p>
          </div>
          <div className="flex flex-col items-end gap-2 shrink-0">
            <ConfidenceBadge confidence={match.confidence} />
            <button type="button" onClick={() => setEditing(true)}
                    data-testid="match-detail-edit"
                    className="button-ghost text-xs">
              Edit match
            </button>
          </div>
        </header>

        <nav className="px-6 py-2 flex flex-wrap gap-2 border-b border-white/5">
          {(
            [
              ["overview", "Overview"],
              ["why",      "Why this confidence"],
              ["dates",    "Dates & guards"],
              ["ai",       "AI verification"],
              ["sources",  "Sources & cluster"],
            ] as Array<[Tab, string]>
          ).map(([k, l]) => (
            <button key={k} onClick={() => setTab(k)}
                    className={`px-3 py-1 rounded-full text-xs transition ${
                      tab === k
                        ? "bg-white/12 text-ink"
                        : "muted hover:text-ink hover:bg-white/5"
                    }`}>{l}</button>
          ))}
        </nav>

        <div className="flex-1 overflow-auto p-6 space-y-4">
          {tab === "overview" && <Overview match={match} payload={p} />}
          {tab === "why"      && <Why match={match} payload={p} />}
          {tab === "dates"    && <Dates match={match} payload={p} />}
          {tab === "ai"       && <AiTab runId={runId} match={match} onPatched={onPatched} />}
          {tab === "sources"  && <Sources match={match} payload={p} />}
        </div>
      </Glass>

      {marcCn && (
        <MarcRecordPopup runId={runId} controlNumber={marcCn}
                         onClose={() => setMarcCn(null)} />
      )}
      {editing && (
        <AuthorityMatchEditDialog
          runId={runId}
          match={match}
          onClose={() => setEditing(false)}
          onSaved={(next) => { onPatched(next); setEditing(false); }}
        />
      )}
    </div>
  );
}


// ── Tabs ────────────────────────────────────────────────────────────────


function Overview({ match, payload }: { match: AuthorityMatch; payload: Record<string, unknown> }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      <Field label="Entity (MARC heading)" value={match.entity_text} />
      <Field label="Matched authority"     value={match.matched_name || "—"} />
      <Field label="Preferred (Latin)"     value={strOr(payload.preferred_name_lat, "—")} />
      <Field label="Role"                  value={match.role || "—"} />
      <Field label="Entity kind"           value={match.entity_kind} />
      <Field label="Source label"          value={match.source || "—"} mono />
      <IdRow label="Mazal / NLI ID"        value={match.mazal_id}
             href={match.mazal_id ? `https://www.nli.org.il/he/authorities/${encodeURIComponent(match.mazal_id)}` : undefined} />
      <IdRow label="VIAF"                  value={match.viaf_id}
             href={match.viaf_id ? `https://viaf.org/viaf/${match.viaf_id}` : undefined} />
      <IdRow label="Wikidata"              value={match.wikidata_qid}
             href={match.wikidata_qid ? `https://www.wikidata.org/wiki/${match.wikidata_qid}` : undefined} />
    </div>
  );
}


function Why({ match, payload }: { match: AuthorityMatch; payload: Record<string, unknown> }) {
  const sources = (payload.sources as string[] | undefined) ?? [];
  const guards  = (payload.guard_flags as string[] | undefined) ?? [];
  const reasoning = strOr(payload.reasoning, "");
  const count = Number(payload.source_count ?? sources.length);

  return (
    <div className="space-y-4">
      <GlassPill as="section" className="p-4 space-y-2">
        <div className="kicker">Pipeline reasoning</div>
        <p className="text-sm leading-relaxed">{reasoning || "—"}</p>
      </GlassPill>

      <div className="grid grid-cols-3 gap-3 text-center">
        <Stat label="Confidence"   value={match.confidence} tone={toneFor(match.confidence)} />
        <Stat label="Sources"      value={String(count)}    tone={count >= 2 ? "biu-sky" : "muted"} />
        <Stat label="Guards fired" value={String(guards.length)} tone={guards.length ? "red" : "biu-sky"} />
      </div>

      <section>
        <div className="kicker mb-1">Sources that contributed</div>
        {sources.length === 0
          ? <p className="muted text-sm italic">No authority source matched this heading.</p>
          : <ul className="flex flex-wrap gap-2">
              {sources.map((s) => (
                <GlassPill as="li" className="px-3 py-0.5 text-xs uppercase tracking-wider text-biu-sky" key={s}>{s}</GlassPill>
              ))}
            </ul>}
      </section>

      <section>
        <div className="kicker mb-1">Guards</div>
        {guards.length === 0
          ? <p className="muted text-sm italic">All guards passed.</p>
          : <ul className="space-y-1">
              {guards.map((g) => (
                <li key={g} className="text-sm">
                  <span className="text-red-300 mr-2">⚠</span>
                  <span className="font-mono">{g}</span>
                  <span className="muted ml-2">{guardExplain(g)}</span>
                </li>
              ))}
            </ul>}
      </section>
    </div>
  );
}


function Dates({match, payload}: {match: AuthorityMatch; payload: Record<string, unknown>}) {
  const msYear = payload.ms_year as number | null | undefined;
  const birth  = payload.birth_year as number | null | undefined;
  const death  = payload.death_year as number | null | undefined;
  const role   = (payload.role_kind as string | undefined) || "other";
  const conflict = (payload.guard_flags as string[] | undefined)?.includes("date_conflict");
  const dateGuardRan = (birth || death) && msYear;
  const hasIds = !!(match.viaf_id || match.wikidata_qid || match.mazal_id);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-3">
        <Stat label="Manuscript year" value={msYear ? String(msYear) : "—"} tone="muted" />
        <Stat label="Born" value={birth ? String(birth) : "—"} tone="muted" />
        <Stat label="Died" value={death ? String(death) : "—"} tone="muted" />
      </div>

      <GlassPill as="section" className="p-4 space-y-2">
        <div className="kicker">Role-aware compatibility</div>
        <p className="text-sm">
          Role kind: <b className="text-ink">{role}</b>.
          {role === "production" && " Death year is checked — a scribe / copyist can't post-date their own death."}
          {role === "authorship" && " Birth year is checked — the author must predate the manuscript."}
          {role === "subject"    && " Death check is silent (manuscripts can be ABOUT someone long dead)."}
          {role === "other"      && " Lenient — only birth is checked."}
        </p>
        <p className="text-sm">
          {conflict
            ? <span className="text-red-300">⚠ Authority Enrichment date guard fired — see the Why tab.</span>
            : dateGuardRan
              ? <span className="text-biu-sky">✓ Dates compatible with the role.</span>
              : hasIds
                ? <span className="muted">
                    — Dates not yet populated. This match has authority IDs (
                    {[match.viaf_id && "VIAF", match.wikidata_qid && "Wikidata", match.mazal_id && "Mazal"]
                      .filter(Boolean).join(" · ")}
                    ) but the birth/death years were not stored — likely a stale authority cache entry
                    from before date extraction was added. Click{" "}
                    <b className="text-ink">Backfill dates</b> in the Authority panel header to pull
                    years from these IDs without re-running the full enrichment.
                  </span>
                : <span className="muted">— Date guard skipped: no candidate birth / death years
                    available from Mazal, VIAF, or Wikidata for this match.</span>}
        </p>
      </GlassPill>
    </div>
  );
}


function AiTab({
  runId, match, onPatched,
}: {
  runId: string;
  match: AuthorityMatch;
  onPatched: (next: AuthorityMatch) => void;
}) {
  const existing = (match.payload?.ai_verdict ?? null) as null | {
    overall: "full" | "partial" | "fail" | "abstain";
    reasoning: string;
    model: string;
    judged_at: string;
  };
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [verdict, setVerdict] = useState(existing);
  const [usedFallback, setUsedFallback] = useState(existing?.model === "heuristic");

  async function run() {
    setBusy(true); setError(null);
    try {
      const r = await Runs.aiVerify(runId, match.id);
      setVerdict({ overall: r.overall, reasoning: r.reasoning, model: r.model, judged_at: r.judged_at });
      setUsedFallback(r.fallback);
      // Reflect back to parent so the table column updates without refetch.
      onPatched({ ...match, payload: { ...match.payload, ai_verdict: r } });
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <GlassPill as="section" className="p-4 space-y-2">
        <div className="kicker">AI verification</div>
        <p className="text-sm muted">
          Asks Gemini whether the candidate authority is the right person
          for this entity, given the MARC context. If you've stored your
          Gemini API key in Settings, the call goes live; otherwise a
          heuristic verdict that uses the same signals fires instead.
        </p>
        <button onClick={run} disabled={busy} className="button-primary text-sm">
          {busy ? "Asking…" : verdict ? "Re-verify" : "Verify with AI"}
        </button>
        {error && <p className="text-red-300 text-sm">{error}</p>}
      </GlassPill>

      {verdict && (
        <Glass as="section" className="p-4 space-y-2">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <VerdictBadge overall={verdict.overall} />
            <span className="kicker">
              {verdict.model}
              {usedFallback && <span className="ml-2 text-yellow-300">heuristic fallback</span>}
            </span>
          </div>
          <p className="text-sm leading-relaxed">{verdict.reasoning}</p>
          <p className="text-xs muted">Judged at {new Date(verdict.judged_at).toLocaleString()}</p>
        </Glass>
      )}
    </div>
  );
}


function Sources({ match, payload }: { match: AuthorityMatch; payload: Record<string, unknown> }) {
  const sources = (payload.sources as string[] | undefined) ?? [];
  const cluster = (payload.cluster_ids as Record<string, string> | undefined) ?? {};

  // Evidence — every claim the system makes about this match needs a
  // source. The MARC field that produced the entity, the matcher that
  // returned the authority record, the URLs we'd cite on Wikidata.
  const marcField = (payload.field as string | undefined)
                 || (payload.marc_field as string | undefined)
                 || (match.role && match.role === "author" ? "100$a / 700$a" : "700$a");

  const evidenceRows: Array<{ src: string; what: string; href?: string }> = [];
  evidenceRows.push({
    src: "MARC",
    what: `extracted from ${marcField} on record ${match.control_number}`,
  });
  if (match.mazal_id) {
    evidenceRows.push({
      src: "Mazal/NLI",
      what: `authority record ${match.mazal_id}`,
      href: `https://www.nli.org.il/he/authorities/${encodeURIComponent(match.mazal_id)}`,
    });
  }
  if (match.viaf_id) {
    evidenceRows.push({
      src: "VIAF",
      what: `cluster ${match.viaf_id}`,
      href: `https://viaf.org/viaf/${match.viaf_id}`,
    });
  }
  if (match.wikidata_qid) {
    evidenceRows.push({
      src: "Wikidata",
      what: `item ${match.wikidata_qid}`,
      href: `https://www.wikidata.org/wiki/${match.wikidata_qid}`,
    });
  }

  return (
    <div className="space-y-4">
      <section>
        <div className="kicker mb-2">Evidence behind this match</div>
        <p className="muted text-xs mb-2">
          Every claim the system makes is anchored to a source. These are
          the sources backing this candidate.
        </p>
        <ul className="space-y-1.5">
          {evidenceRows.map((e, i) => (
            <GlassPill as="li" className="px-3 py-1.5 text-sm flex items-baseline justify-between gap-3" key={i}>
              <span><b className="text-ink">{e.src}</b> · {e.what}</span>
              {e.href && (
                <a href={e.href} target="_blank" rel="noreferrer"
                   className="text-biu-sky text-xs hover:underline shrink-0">open ↗</a>
              )}
            </GlassPill>
          ))}
        </ul>
      </section>

      <KimaGeoSection payload={payload} />

      <section>
        <div className="kicker mb-2">Authority sources matched</div>
        <ul className="grid grid-cols-2 gap-2">
          {["mazal", "viaf", "kima", "wikidata"].map((s) => {
            const on = sources.includes(s);
            return (
              <Glass as="li" className="px-3 py-2 flex items-center justify-between" key={s}>
                <span className={`${on ? "text-ink" : "muted"} uppercase text-xs tracking-wider`}>{s}</span>
                {on
                  ? <span className="text-biu-sky text-xs">✓ matched</span>
                  : <span className="muted text-xs">—</span>}
              </Glass>
            );
          })}
        </ul>
      </section>

      <section>
        <div className="kicker mb-2">VIAF cluster identifiers</div>
        {Object.keys(cluster).length === 0
          ? <p className="muted text-sm italic">
              {match.viaf_id
                ? "VIAF matched but no cluster IDs harvested for this candidate."
                : "No VIAF match — cluster harvesting only runs when VIAF resolves."}
            </p>
          : (
            <ul className="grid grid-cols-1 md:grid-cols-2 gap-2 text-sm">
              {Object.entries(cluster).map(([k, v]) => (
                <GlassPill as="li" className="px-3 py-1 flex justify-between" key={k}>
                  <span className="kicker">{k}</span>
                  <span className="font-mono text-xs">{v}</span>
                </GlassPill>
              ))}
            </ul>
          )}
      </section>

      <GlassPill as="section" className="p-3">
        <div className="kicker mb-1">Raw payload</div>
        <pre className="text-[11px] font-mono whitespace-pre-wrap leading-snug"
             style={{ background: "rgba(0,0,0,0.36)", padding: 10, borderRadius: 10, border: "1px solid var(--line)" }}>
          {JSON.stringify(match.payload, null, 2)}
        </pre>
      </GlassPill>
    </div>
  );
}


function KimaGeoSection({payload}: {payload: Record<string, unknown>}) {
  const lat = payload.kima_lat as number | string | undefined;
  const lon = payload.kima_lon as number | string | undefined;
  const heb = strOr(payload.kima_heb, "");
  const rom = strOr(payload.kima_rom, "");
  const geonames = strOr(payload.kima_geonames, "");
  if (lat == null && lon == null && !heb && !rom) return null;
  const latN = lat != null ? Number(lat) : NaN;
  const lonN = lon != null ? Number(lon) : NaN;
  const osmHref = !Number.isNaN(latN) && !Number.isNaN(lonN)
    ? `https://www.openstreetmap.org/?mlat=${latN}&mlon=${lonN}#map=12/${latN}/${lonN}`
    : undefined;
  return (
    <GlassPill as="section" className="p-4 space-y-2">
      <div className="kicker">KIMA location</div>
      {heb ? <p className="text-sm"><span className="muted">Hebrew:</span> {heb}</p> : null}
      {rom ? <p className="text-sm"><span className="muted">Romanized:</span> {rom}</p> : null}
      {(lat != null || lon != null) && (
        <p className="text-sm font-mono">
          {lat ?? "—"}, {lon ?? "—"}
          {osmHref && (
            <a href={osmHref} target="_blank" rel="noreferrer"
               className="ml-2 text-biu-sky text-xs hover:underline">OpenStreetMap ↗</a>
          )}
        </p>
      )}
      {geonames ? (
        <p className="text-xs muted">GeoNames: {geonames}</p>
      ) : null}
    </GlassPill>
  );
}


// ── tiny presentational helpers ─────────────────────────────────────────


function Field({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div className="kicker mb-1">{label}</div>
      <p className={`text-sm ${mono ? "font-mono" : ""}`}>{value}</p>
    </div>
  );
}


function IdRow({
  label, value, href,
}: { label: string; value: string; href?: string }) {
  return (
    <div>
      <div className="kicker mb-1">{label}</div>
      <p className="text-sm font-mono">
        {value
          ? (href
              ? <a href={href} target="_blank" rel="noreferrer" className="text-biu-sky hover:underline">{value}</a>
              : value)
          : <span className="muted">—</span>}
      </p>
    </div>
  );
}


function Stat({ label, value, tone }: { label: string; value: string; tone: string }) {
  const cls =
    tone === "biu-sky" ? "text-biu-sky"
    : tone === "red"   ? "text-red-300"
    : tone === "yellow"? "text-yellow-300"
    : "text-ink";
  return (
    <GlassPill as="div" className="px-3 py-2">
      <div className="kicker">{label}</div>
      <div className={`text-xl font-semibold ${cls}`}>{value}</div>
    </GlassPill>
  );
}


export function ConfidenceBadge({ confidence }: { confidence: string }) {
  const cls =
    confidence === "high"   ? "text-biu-sky"
    : confidence === "medium" ? "text-yellow-300"
    : "muted";
  return (
    <GlassPill className={`px-3 py-1 text-[10px] kicker shrink-0 ${cls}`}>
      {confidence}
    </GlassPill>
  );
}


export function VerdictBadge({overall}: {overall: string}) {
  const palette: Record<string, string> = {
    full:    "text-biu-sky",
    partial: "text-yellow-300",
    fail:    "text-red-300",
    abstain: "muted",
  };
  const label: Record<string, string> = {
    full:    "Looks right",
    partial: "Partly right",
    fail:    "Got it wrong",
    abstain: "Unsure",
  };
  return (
    <GlassPill className={`px-3 py-1 text-xs kicker ${palette[overall] ?? "muted"}`}>
      {label[overall] ?? overall}
    </GlassPill>
  );
}


function toneFor(confidence: string): string {
  if (confidence === "high") return "biu-sky";
  if (confidence === "medium") return "yellow";
  return "muted";
}


function strOr(v: unknown, fallback: string): string {
  return typeof v === "string" && v.length > 0 ? v : fallback;
}


const _GUARD_NOTES: Record<string, string> = {
  date_conflict:       "Manuscript date is incompatible with the candidate's lifespan + role.",
  name_drift:          "Preferred form drifts from the MARC heading.",
  weaker_alternative:  "Secondary candidate offered alongside a stronger primary.",
};
function guardExplain(g: string): string {
  return _GUARD_NOTES[g] ?? "";
}
