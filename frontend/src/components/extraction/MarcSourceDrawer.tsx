/**
 * MarcSourceDrawer — slide-in right-side drawer that shows the full
 * MARC record an extracted entity came from.
 *
 * Opens whenever the entity table's 👁 column is clicked or the user
 * picks a row in the verification modal's verdict table.
 *
 * Rendering rules:
 *  - Each MARC field is shown on its own row: ``245 ▶ <text>``.
 *  - When ``highlightEntity`` is set, every occurrence of that
 *    entity's ``text`` inside any MARC field value is wrapped in a
 *    yellow ``<mark>``. Substring search — no offset arithmetic — so
 *    a missing or stale ``start/end`` pair is tolerated gracefully.
 *  - The "Entities in this record" section at the bottom lets the
 *    curator jump between the record's other entities without
 *    re-opening the drawer.
 *
 * The drawer is intentionally NOT modal: the entity table behind it
 * stays interactive. A pin checkbox keeps it open across selection
 * changes (parent component reads ``pinned`` via the optional
 * ``onPinnedChange`` callback to keep the drawer mounted when the
 * highlight changes).
 */

import { useEffect, useMemo, useState } from "react";

import { MarcSourceApi, type MarcSource, type MarcSourceEntity } from "@/api/marcSource";


export interface MarcSourceDrawerHighlight {
  id:    string;
  text:  string;
  start: number | null;
  end:   number | null;
}


export interface MarcSourceDrawerProps {
  runId: string;
  /** Control number of the MARC record to show. ``null`` ⇒ drawer
   *  closed (parent unmounts or hides). */
  controlNumber: string | null;
  /** Entity whose span(s) should be highlighted yellow. Optional —
   *  when omitted the drawer just shows the record. */
  highlightEntity?: MarcSourceDrawerHighlight | null;
  onClose: () => void;
  /** Called when the user toggles the pin checkbox. The parent
   *  decides whether to keep the drawer mounted across selection
   *  changes. */
  onPinnedChange?: (pinned: boolean) => void;
}


export function MarcSourceDrawer(props: MarcSourceDrawerProps) {
  const { runId, controlNumber, highlightEntity, onClose, onPinnedChange } = props;

  const [data,    setData]    = useState<MarcSource | null>(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);
  const [pinned,  setPinned]  = useState(false);

  // Reset on control-number change.
  useEffect(() => {
    if (!controlNumber) { setData(null); setError(null); return; }
    let cancelled = false;
    setLoading(true); setError(null);
    MarcSourceApi.get(runId, controlNumber)
      .then((src) => { if (!cancelled) setData(src); })
      .catch((e: Error) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [runId, controlNumber]);

  // Esc closes the drawer (parent owns the visibility).
  useEffect(() => {
    if (!controlNumber) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !pinned) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [controlNumber, pinned, onClose]);

  const open = controlNumber != null;

  // Build the list of "blue" highlight strings — every entity in the
  // record other than the currently selected one.
  const otherEntityTexts = useMemo(() => {
    if (!data) return [] as string[];
    const out: string[] = [];
    for (const e of data.entities) {
      if (highlightEntity && e.id === highlightEntity.id) continue;
      const t = (e.text ?? "").trim();
      if (t.length >= 2) out.push(t);
    }
    return out;
  }, [data, highlightEntity]);

  return (
    <aside
      aria-hidden={!open}
      style={{
        position:       "fixed",
        top:            0,
        right:          0,
        bottom:         0,
        width:          "min(560px, 92vw)",
        zIndex:         40,
        transform:      open ? "translateX(0)" : "translateX(110%)",
        transition:     "transform 220ms ease-out",
        pointerEvents:  open ? "auto" : "none",
      }}
      className="glass p-0 flex flex-col"
    >
      {/* Header */}
      <header className="flex items-start justify-between gap-3 px-4 pt-4 pb-2 border-b border-white/5">
        <div className="min-w-0">
          <div className="kicker">MARC source</div>
          <h3 className="text-sm font-semibold truncate font-mono" title={controlNumber ?? ""}>
            {controlNumber ?? ""}
          </h3>
        </div>
        <div className="flex items-center gap-2 text-[11px]">
          <label className="muted flex items-center gap-1 cursor-pointer select-none"
                 title="Keep this drawer open while you browse the table.">
            <input type="checkbox" checked={pinned}
                   onChange={(e) => { setPinned(e.target.checked); onPinnedChange?.(e.target.checked); }} />
            Pin
          </label>
          <button onClick={onClose}
                  className="button-ghost !py-0.5 !px-2 text-xs"
                  title="Close (Esc)">
            ✕
          </button>
        </div>
      </header>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4 text-xs">
        {loading && (
          <div className="muted italic">Loading MARC record…</div>
        )}
        {error && (
          <div className="text-red-300 text-xs">
            Failed to load MARC source: {error}
          </div>
        )}
        {data && !loading && !error && (
          <>
            <MarcRecordFields
              marc={data.marc}
              primaryHighlight={highlightEntity?.text ?? ""}
              secondaryHighlights={otherEntityTexts}
            />
            <EntityListing
              entities={data.entities}
              activeId={highlightEntity?.id ?? null}
            />
          </>
        )}
      </div>
    </aside>
  );
}


// ── MARC field renderer ──────────────────────────────────────────────


function MarcRecordFields({
  marc, primaryHighlight, secondaryHighlights,
}: {
  marc: Record<string, unknown>;
  /** Yellow highlight — the entity the curator is reviewing. */
  primaryHighlight: string;
  /** Blue highlights — other entities present in the same record. */
  secondaryHighlights: string[];
}) {
  const rows = useMemo(() => flattenMarc(marc), [marc]);

  if (rows.length === 0) {
    return <div className="muted italic">No MARC fields available.</div>;
  }

  return (
    <section>
      <div className="kicker mb-2">Fields ({rows.length})</div>
      <div className="space-y-1">
        {rows.map((row, i) => (
          <div key={`${row.tag}-${i}`} className="flex gap-2 items-baseline">
            <span className="font-mono text-biu-sky shrink-0 w-12 text-right">{row.tag}</span>
            <span className="muted text-[10px] shrink-0">▶</span>
            <span className="text-ink/90 flex-1 break-words leading-relaxed" dir="auto">
              <HighlightedText
                value={row.value}
                primary={primaryHighlight}
                secondaries={secondaryHighlights}
              />
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}


/** Walk the MARC dict and produce a flat list of ``(tag, value)``
 *  rows for display. The backend response is ``marc_ingest.py``-shaped
 *  so values may be strings, lists, or nested objects depending on the
 *  field — we coerce everything to a printable string.
 *
 *  Common keys we know about:
 *   - ``control_number``      → single string
 *   - ``leader``              → single string
 *   - ``fields``              → list of {tag, value, subfields[]}
 *   - top-level structured slots (title, authors, …) at sibling keys.
 *
 *  We honour both shapes — a ``fields`` list when present, plus any
 *  top-level scalar/array keys for the structured slots.
 */
function flattenMarc(marc: Record<string, unknown>): Array<{ tag: string; value: string }> {
  const out: Array<{ tag: string; value: string }> = [];

  // 1) Raw MARC fields when present.
  const rawFields = marc.fields;
  if (Array.isArray(rawFields)) {
    for (const f of rawFields) {
      if (typeof f !== "object" || f == null) continue;
      const rec = f as Record<string, unknown>;
      const tag = String(rec.tag ?? rec.code ?? "");
      const value = stringifyField(rec);
      if (tag && value) out.push({ tag, value });
    }
  }

  // 2) Top-level structured slots (title, contributors, …) when the
  //    backend hasn't materialised them inside ``fields`` already.
  //    We render them as labelled rows so the curator sees the
  //    higher-level interpretation too.
  const STRUCTURED_KEYS: Array<[string, string]> = [
    ["title",              "title"],
    ["title_variants",     "alt titles"],
    ["uniform_title",      "uniform title"],
    ["alternate_titles",   "alt titles"],
    ["authors",            "authors"],
    ["contributors",       "contributors"],
    ["subjects",           "subjects"],
    ["genre_form",         "genre/form"],
    ["genres",             "genres"],
    ["acquisition_source", "acquisition"],
    ["former_owners",      "former owners"],
    ["ownership_history",  "ownership"],
    ["series",             "series"],
    ["contents",           "contents"],
    ["works",              "works"],
    ["places",             "places"],
    ["related_places",     "related places"],
    ["colophon_text",      "colophon"],
  ];
  for (const [key, label] of STRUCTURED_KEYS) {
    if (!(key in marc)) continue;
    const value = stringifyValue(marc[key]);
    if (value) out.push({ tag: label, value });
  }

  return out;
}


function stringifyField(rec: Record<string, unknown>): string {
  // MARC subfields shape: {tag, indicator1, indicator2, subfields: [{code, value}, …]}
  const subfields = rec.subfields;
  if (Array.isArray(subfields)) {
    const parts: string[] = [];
    for (const sf of subfields) {
      if (typeof sf !== "object" || sf == null) continue;
      const sub = sf as Record<string, unknown>;
      const code = String(sub.code ?? "");
      const v    = String(sub.value ?? "").trim();
      if (!v) continue;
      parts.push(code ? `‡${code} ${v}` : v);
    }
    if (parts.length > 0) return parts.join(" ");
  }
  // Simple value field.
  if (typeof rec.value === "string") return rec.value;
  if (typeof rec.text === "string")  return rec.text;
  return "";
}


function stringifyValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    return value.map((v) => stringifyValue(v)).filter(Boolean).join(" · ");
  }
  if (typeof value === "object") {
    const rec = value as Record<string, unknown>;
    // Common shapes.
    if (typeof rec.text === "string") return rec.text;
    if (typeof rec.value === "string") return rec.value;
    if (typeof rec.name === "string") return rec.name;
    // Fallback: short JSON dump.
    try { return JSON.stringify(rec); } catch { return ""; }
  }
  return "";
}


/** Render a string with primary (yellow) and secondary (blue)
 *  highlights wrapped around every substring match. Case-insensitive.
 *  Primary wins where they overlap. */
function HighlightedText({
  value, primary, secondaries,
}: {
  value: string;
  primary: string;
  secondaries: string[];
}) {
  const parts = useMemo(
    () => splitHighlights(value, primary, secondaries),
    [value, primary, secondaries],
  );
  return (
    <>
      {parts.map((p, i) => {
        if (p.kind === "plain") return <span key={i}>{p.text}</span>;
        if (p.kind === "primary") {
          return (
            <mark key={i}
              style={{
                background: "rgba(253, 224, 71, 0.45)",
                color:      "var(--ink)",
                padding:    "0 2px",
                borderRadius: 3,
              }}>
              {p.text}
            </mark>
          );
        }
        return (
          <mark key={i}
            style={{
              background: "rgba(127, 196, 255, 0.28)",
              color:      "var(--ink)",
              padding:    "0 2px",
              borderRadius: 3,
            }}>
            {p.text}
          </mark>
        );
      })}
    </>
  );
}


type HighlightPart =
  | { kind: "plain";   text: string }
  | { kind: "primary"; text: string }
  | { kind: "secondary"; text: string };


/** Tokenise ``value`` into a sequence of (plain | primary | secondary)
 *  segments by scanning for primary substring matches first, then
 *  filling the gaps with secondary matches. Pure function, no DOM. */
function splitHighlights(
  value: string,
  primary: string,
  secondaries: string[],
): HighlightPart[] {
  if (!value) return [];
  // Build a list of [start, end, kind] ranges from primary + secondaries.
  type Range = { start: number; end: number; kind: "primary" | "secondary" };
  const ranges: Range[] = [];

  function pushAll(needle: string, kind: "primary" | "secondary") {
    const n = needle.trim();
    if (n.length < 2) return;
    const lcVal = value.toLowerCase();
    const lcN   = n.toLowerCase();
    let from = 0;
    while (true) {
      const idx = lcVal.indexOf(lcN, from);
      if (idx < 0) break;
      ranges.push({ start: idx, end: idx + n.length, kind });
      from = idx + n.length;
    }
  }

  if (primary) pushAll(primary, "primary");
  for (const s of secondaries) pushAll(s, "secondary");

  if (ranges.length === 0) return [{ kind: "plain", text: value }];

  // Sort by start; primary wins on overlap.
  ranges.sort((a, b) => a.start - b.start || (a.kind === "primary" ? -1 : 1));

  // Greedy merge: emit non-overlapping ranges, primary taking
  // precedence where ranges overlap.
  const chosen: Range[] = [];
  for (const r of ranges) {
    const last = chosen[chosen.length - 1];
    if (!last || r.start >= last.end) {
      chosen.push(r);
      continue;
    }
    if (r.kind === "primary" && last.kind !== "primary") {
      // Replace the overlapped tail with the primary range.
      chosen[chosen.length - 1] = r;
    }
    // Otherwise skip — last range stays.
  }

  // Emit alternating plain / highlight parts.
  const parts: HighlightPart[] = [];
  let cursor = 0;
  for (const r of chosen) {
    if (r.start > cursor) {
      parts.push({ kind: "plain", text: value.slice(cursor, r.start) });
    }
    parts.push({ kind: r.kind, text: value.slice(r.start, r.end) });
    cursor = r.end;
  }
  if (cursor < value.length) {
    parts.push({ kind: "plain", text: value.slice(cursor) });
  }
  return parts;
}


// ── Entity listing (jump-to widget) ──────────────────────────────────


function EntityListing({
  entities, activeId,
}: {
  entities: MarcSourceEntity[];
  activeId: string | null;
}) {
  if (entities.length === 0) {
    return (
      <section>
        <div className="kicker mb-1">Entities</div>
        <div className="muted italic text-[11px]">None.</div>
      </section>
    );
  }
  return (
    <section>
      <div className="kicker mb-1">Entities in this record ({entities.length})</div>
      <ul className="space-y-1 text-[11px]">
        {entities.map((e) => (
          <li
            key={e.id}
            className={`flex gap-2 items-baseline px-2 py-1 rounded ${
              activeId === e.id ? "bg-white/[0.06]" : ""
            }`}
          >
            <span className="text-ink/95 truncate flex-1" dir="auto">{e.text}</span>
            <span className="muted shrink-0 font-mono text-[10px]">{e.type}</span>
            {e.role && e.role !== e.type && (
              <span className="text-biu-sky shrink-0 font-mono text-[10px]">{e.role}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
