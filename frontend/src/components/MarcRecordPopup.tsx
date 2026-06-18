/**
 * Searchable MARC-record popup — opened from anywhere that shows a
 * control number (Run table, Match Detail, Studio item panel).
 *
 * The popup loads the record once, indexes every leaf key/value, and
 * renders a flat list of "FIELD$SUBFIELD = value" rows. The search box
 * does live full-text filtering with case-insensitive substring
 * matching + highlighted hits, so curators can ⌘F-style find a name
 * inside the full MARC record without scrolling through JSON.
 */

import { useEffect, useMemo, useState } from "react";

import { api, ApiError } from "@/api/client";
import type { RunMarcRecord } from "@/api/runs";
import {Glass} from "@/components/glass";

interface Props {
  runId: string;
  controlNumber: string;
  onClose: () => void;
}


export function MarcRecordPopup({ runId, controlNumber, onClose }: Props) {
  const [record, setRecord] = useState<RunMarcRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    setRecord(null); setError(null);
    api.get<RunMarcRecord>(`/runs/${runId}/records/${encodeURIComponent(controlNumber)}`)
       .then(setRecord)
       .catch((e) => setError(e instanceof ApiError ? e.detail : String(e)));
  }, [runId, controlNumber]);

  // ESC closes
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const rows = useMemo(() => flatten(record?.marc ?? {}), [record]);
  const q = query.trim().toLowerCase();
  const filtered = q
    ? rows.filter((r) => r.key.toLowerCase().includes(q) || r.value.toLowerCase().includes(q))
    : rows;

  return (
    <div className="fixed inset-0 z-50 grid place-items-center p-4 bg-black/60"
         onClick={onClose}>
      <Glass className="max-w-3xl w-full max-h-[85vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
        <header className="p-5 pb-3 border-b border-white/10 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="kicker">MARC record</div>
            <h3 className="font-mono break-all">{controlNumber}</h3>
            <p className="muted text-xs">
              <a className="text-biu-sky hover:underline" target="_blank" rel="noreferrer"
                 href={`https://www.nli.org.il/he/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH${controlNumber}`}>
                Open at NLI ↗
              </a>
            </p>
          </div>
          <button onClick={onClose} className="button-ghost text-sm shrink-0">Close (ESC)</button>
        </header>

        <div className="px-5 pt-3 pb-2 border-b border-white/5">
          <input value={query} onChange={(e) => setQuery(e.target.value)}
                 placeholder="Find in record — try a name, a year, a MARC tag like 100$a…"
                 autoFocus
                 className="input-glass !py-2 text-sm" />
          <p className="muted text-[11px] mt-1">
            {filtered.length} of {rows.length} fields
            {q && <> · matching <b className="text-ink">{q}</b></>}
          </p>
        </div>

        <div className="flex-1 overflow-auto p-4 space-y-1">
          {error && <p className="text-danger text-sm">{error}</p>}
          {!record && !error && <p className="muted text-sm">Loading…</p>}
          {filtered.map((r) => (
            <div key={r.key}
                 className="grid grid-cols-[140px_1fr] gap-3 text-xs py-1 border-b border-white/5">
              <code className="text-biu-sky font-mono">
                {highlight(r.key, q)}
              </code>
              <span className="whitespace-pre-wrap break-words">
                {highlight(r.value, q)}
              </span>
            </div>
          ))}
          {filtered.length === 0 && record && (
            <p className="muted text-sm italic text-center py-6">No matches.</p>
          )}
        </div>
      </Glass>
    </div>
  );
}


// ── helpers ─────────────────────────────────────────────────────────────


interface FlatRow { key: string; value: string; }


function flatten(obj: unknown, prefix = ""): FlatRow[] {
  if (obj == null) return [];
  if (typeof obj !== "object") {
    return [{ key: prefix || "(value)", value: String(obj) }];
  }
  if (Array.isArray(obj)) {
    return obj.flatMap((v, i) => flatten(v, prefix ? `${prefix}[${i}]` : `[${i}]`));
  }
  const rows: FlatRow[] = [];
  for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (v == null || v === "") continue;
    if (typeof v === "object") {
      rows.push(...flatten(v, path));
    } else {
      rows.push({ key: path, value: String(v) });
    }
  }
  return rows;
}


function highlight(text: string, q: string) {
  if (!q) return text;
  const lc = text.toLowerCase();
  const ql = q.toLowerCase();
  const out: (string | JSX.Element)[] = [];
  let i = 0;
  while (i < text.length) {
    const j = lc.indexOf(ql, i);
    if (j === -1) { out.push(text.slice(i)); break; }
    if (j > i) out.push(text.slice(i, j));
    out.push(
      <mark key={j} className="rounded-sm px-0.5"
            style={{ background: "rgba(119,204,229,0.32)", color: "var(--ink)" }}>
        {text.slice(j, j + q.length)}
      </mark>,
    );
    i = j + q.length;
  }
  return <>{out}</>;
}
