import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { ApiError } from "@/api/client";
import { useProjectEvents } from "@/api/realtime";
import {
  Runs, type AuthorityMatch, type RunDetail as Detail,
} from "@/api/runs";
import {
  applyConditions, type FilterCondition, StructuredFilter,
} from "@/components/StructuredFilter";
import {
  ConfidenceBadge, MatchDetailDialog, VerdictBadge,
} from "@/components/MatchDetailDialog";
import { MarcRecordPopup } from "@/components/MarcRecordPopup";
import { SelectAllVisible } from "@/components/SelectAllVisible";

const COLUMNS = [
  { key: "control_number", label: "Record",     kind: "text" as const, sortable: true  },
  { key: "entity_text",    label: "Entity",     kind: "text" as const, sortable: true  },
  { key: "role",           label: "Role",       kind: "text" as const, sortable: true  },
  { key: "matched_name",   label: "Matched",    kind: "text" as const, sortable: true  },
  { key: "confidence",     label: "Confidence", kind: "text" as const, sortable: true  },
  { key: "source",         label: "Sources",    kind: "text" as const, sortable: true  },
  { key: "ai_verdict",     label: "AI verdict", kind: "text" as const, sortable: true  },
  { key: "approved",       label: "Approved",   kind: "boolean" as const, sortable: true },
];

type SortDir = "asc" | "desc";
const CONFIDENCE_ORDER: Record<string, number> = { low: 0, medium: 1, high: 2 };
const VERDICT_ORDER:    Record<string, number> = { fail: 0, partial: 1, abstain: 2, pass: 3 };

export default function RunDetail() {
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [conditions, setConditions] = useState<FilterCondition[]>([]);
  const [marcPopupCn, setMarcPopupCn] = useState<string | null>(null);
  const [openMatch, setOpenMatch] = useState<AuthorityMatch | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Sort state — persisted across reloads so curators don't lose their place.
  const [sortKey, setSortKey] = useState<string | null>(() =>
    localStorage.getItem("mhm.runDetail.sortKey") || null);
  const [sortDir, setSortDir] = useState<SortDir>(() =>
    (localStorage.getItem("mhm.runDetail.sortDir") as SortDir) || "asc");
  useEffect(() => {
    if (sortKey) localStorage.setItem("mhm.runDetail.sortKey", sortKey);
    else         localStorage.removeItem("mhm.runDetail.sortKey");
    localStorage.setItem("mhm.runDetail.sortDir", sortDir);
  }, [sortKey, sortDir]);

  async function refresh() {
    if (!runId) return;
    try {
      setRun(await Runs.get(runId));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }
  useEffect(() => { void refresh(); }, [runId]);

  useProjectEvents(run?.project_id, (msg) => {
    if (msg.type.startsWith("match.") || msg.type === "snapshot.restored") {
      void refresh();
    }
  });

  async function toggle(m: AuthorityMatch) {
    if (!runId) return;
    try {
      const updated = await Runs.setApproval(runId, m.id, !m.approved);
      patchMatch(updated);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function bulk(approved: boolean) {
    if (!runId || selected.size === 0) return;
    try {
      const updated = await Runs.bulkApprove(runId, Array.from(selected), approved);
      setRun((prev) => {
        if (!prev) return prev;
        const m = new Map(prev.matches.map((x) => [x.id, x] as const));
        for (const u of updated) m.set(u.id, u);
        return { ...prev, matches: Array.from(m.values()) };
      });
      setSelected(new Set());
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  function patchMatch(next: AuthorityMatch) {
    setRun((prev) =>
      prev
        ? { ...prev, matches: prev.matches.map((x) => (x.id === next.id ? next : x)) }
        : prev,
    );
    if (openMatch && openMatch.id === next.id) setOpenMatch(next);
  }

  function openRecord(cn: string) {
    setMarcPopupCn(cn);
  }

  const distinct = useMemo<Record<string, string[]>>(() => {
    if (!run) return {};
    const acc: Record<string, Set<string>> = {};
    for (const m of run.matches) {
      for (const c of COLUMNS) {
        acc[c.key] = acc[c.key] ?? new Set();
        acc[c.key].add(cellString(m, c.key));
      }
    }
    const out: Record<string, string[]> = {};
    for (const k of Object.keys(acc)) out[k] = Array.from(acc[k]).filter(Boolean).sort();
    return out;
  }, [run]);

  const filtered = useMemo(() => {
    if (!run) return [];
    const rows = applyConditions(run.matches, conditions, (m, col) => cellString(m, col));
    if (!sortKey) return rows;
    const cmp = sortComparator(sortKey, sortDir);
    return [...rows].sort(cmp);
  }, [run, conditions, sortKey, sortDir]);

  // Cycle: not-sorted → asc → desc → not-sorted.
  function toggleSort(col: string) {
    if (sortKey !== col)     { setSortKey(col); setSortDir("asc"); return; }
    if (sortDir === "asc")   { setSortDir("desc");                  return; }
    setSortKey(null); setSortDir("asc");
  }

  function selectAllVisible() {
    const next = new Set(selected);
    filtered.forEach((m) => next.add(m.id));
    setSelected(next);
  }
  function clearSelection() { setSelected(new Set()); }
  // Count how many of the currently-selected rows are ALSO visible —
  // used for the indeterminate state on the SelectAllVisible widget.
  const selectedInVisible = filtered.reduce((n, m) => n + (selected.has(m.id) ? 1 : 0), 0);

  if (error)
    return <Layout><div className="glass p-6 text-red-300">{error}</div></Layout>;
  if (!run)
    return <Layout><p className="muted">Loading run…</p></Layout>;

  return (
    <Layout>
      <div className="space-y-6">
        <section className="glass p-6 space-y-2">
          <div className="kicker">
            Run · <Link to={`/projects/${run.project_id}`} className="hover:text-ink underline">back to project</Link>
          </div>
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 className="text-2xl font-semibold">{run.name}</h2>
            <div className="flex items-center gap-3">
              <Link to={`/runs/${run.id}/wikidata-studio`} className="button-ghost text-sm">
                Wikidata Studio →
              </Link>
              <StatusPill status={run.status} />
            </div>
          </div>
          <p className="muted text-sm">
            {run.record_count} record{run.record_count === 1 ? "" : "s"} ·{" "}
            {run.match_count} candidate match{run.match_count === 1 ? "" : "es"} ·{" "}
            {new Date(run.created_at).toLocaleString()}
          </p>
          {run.error && <p className="text-red-300 text-sm">{run.error}</p>}
        </section>

        <section className="glass p-6 space-y-4">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div>
              <div className="kicker">Authority candidates</div>
              <h3 className="text-lg font-medium">
                Review &amp; approve · <span className="muted">{filtered.length} of {run.matches.length}</span>
              </h3>
            </div>
            <div className="flex gap-2">
              <button disabled={selected.size === 0} onClick={() => bulk(true)}  className="button-primary !py-1.5 text-sm">Approve selected</button>
              <button disabled={selected.size === 0} onClick={() => bulk(false)} className="button-ghost   !py-1.5 text-sm">Unapprove selected</button>
            </div>
          </div>

          <StructuredFilter
            columns={COLUMNS}
            distinctValues={distinct}
            conditions={conditions}
            onChange={setConditions}
          />

          <div className="flex items-center justify-between gap-3 flex-wrap pt-1">
            <SelectAllVisible
              visibleCount={filtered.length}
              selectedCount={selectedInVisible}
              onSelectAll={selectAllVisible}
              onClear={clearSelection}
              label="matches" />
            {sortKey && (
              <button onClick={() => setSortKey(null)}
                      className="text-xs text-biu-sky hover:underline">
                Clear sort
              </button>
            )}
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead className="muted text-left">
                <tr className="border-b border-white/5">
                  <th className="py-2 pr-2"></th>
                  {COLUMNS.map((c) => (
                    <th key={c.key} className="py-2 pr-3 select-none">
                      {c.sortable
                        ? <button onClick={() => toggleSort(c.key)}
                                  className="inline-flex items-center gap-1 hover:text-ink transition">
                            <span>{c.label}</span>
                            <SortGlyph active={sortKey === c.key} dir={sortDir} />
                          </button>
                        : c.label}
                    </th>
                  ))}
                  <th className="py-2 pr-3"></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((m) => {
                  const p = (m.payload ?? {}) as Record<string, unknown>;
                  const ai = (p.ai_verdict ?? null) as null | { overall: string };
                  const guards = (p.guard_flags as string[] | undefined) ?? [];
                  const sourceCount = Number(p.source_count ?? 0);
                  return (
                    <tr key={m.id} className="border-b border-white/5 hover:bg-white/[0.03] transition">
                      <td className="py-2 pr-2">
                        <input type="checkbox"
                               checked={selected.has(m.id)}
                               onChange={(e) => {
                                 const next = new Set(selected);
                                 if (e.target.checked) next.add(m.id); else next.delete(m.id);
                                 setSelected(next);
                               }} />
                      </td>
                      <td className="py-2 pr-3">
                        <button onClick={() => openRecord(m.control_number)}
                                className="text-biu-sky hover:underline font-mono text-xs">
                          {m.control_number}
                        </button>
                      </td>
                      <td className="py-2 pr-3 max-w-xs truncate">{m.entity_text}</td>
                      <td className="py-2 pr-3"><span className="kicker">{m.role || "—"}</span></td>
                      <td className="py-2 pr-3 max-w-xs truncate">
                        {m.matched_name || <span className="muted">—</span>}
                        {guards.length > 0 && (
                          <span className="text-red-300 ml-1" title={guards.join(", ")}>⚠</span>
                        )}
                      </td>
                      <td className="py-2 pr-3">
                        <span title={confidenceTooltip(m)} className="cursor-help">
                          <ConfidenceBadge confidence={m.confidence} />
                        </span>
                      </td>
                      <td className="py-2 pr-3">
                        <SourcesCell match={m} sourceCount={sourceCount} />
                      </td>
                      <td className="py-2 pr-3">
                        {ai
                          ? <VerdictBadge overall={ai.overall} />
                          : <span className="muted text-xs italic">—</span>}
                      </td>
                      <td className="py-2 pr-3">
                        <input type="checkbox" checked={m.approved} onChange={() => toggle(m)} />
                      </td>
                      <td className="py-2 pr-1 text-right">
                        <button onClick={() => setOpenMatch(m)}
                                className="button-ghost text-xs">Details</button>
                      </td>
                    </tr>
                  );
                })}
                {filtered.length === 0 && (
                  <tr><td colSpan={COLUMNS.length + 2} className="py-6 text-center muted">No rows match this filter.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      {marcPopupCn && runId && (
        <MarcRecordPopup runId={runId} controlNumber={marcPopupCn}
                         onClose={() => setMarcPopupCn(null)} />
      )}
      {openMatch && (
        <MatchDetailDialog
          runId={runId!}
          match={openMatch}
          onClose={() => setOpenMatch(null)}
          onPatched={patchMatch}
        />
      )}
    </Layout>
  );
}


// ── helpers ─────────────────────────────────────────────────────────────


function cellString(m: AuthorityMatch, col: string): string {
  if (col === "ai_verdict") {
    const v = m.payload?.ai_verdict as { overall?: string } | undefined;
    return v?.overall ?? "";
  }
  if (col === "source") {
    const list = m.payload?.sources as string[] | undefined;
    if (list?.length) return list.join(",");
  }
  return String((m as unknown as Record<string, unknown>)[col] ?? "");
}


/** Tiny up/down/idle indicator next to a sortable header. */
function SortGlyph({ active, dir }: { active: boolean; dir: SortDir }) {
  if (!active)        return <span className="muted text-[9px]">⇅</span>;
  if (dir === "asc")  return <span className="text-biu-sky text-[10px]">↑</span>;
  return                    <span className="text-biu-sky text-[10px]">↓</span>;
}


/** Returns a stable comparator for the given key + direction. Uses
 *  per-column orderings for confidence and ai_verdict (those aren't
 *  alphabetical) and source_count first for the Sources column. */
function sortComparator(
  key: string, dir: SortDir,
): (a: AuthorityMatch, b: AuthorityMatch) => number {
  const mul = dir === "asc" ? 1 : -1;
  return (a, b) => {
    let r = 0;
    if (key === "confidence") {
      r = (CONFIDENCE_ORDER[a.confidence] ?? -1) - (CONFIDENCE_ORDER[b.confidence] ?? -1);
    } else if (key === "ai_verdict") {
      const av = (a.payload?.ai_verdict as { overall?: string } | undefined)?.overall ?? "";
      const bv = (b.payload?.ai_verdict as { overall?: string } | undefined)?.overall ?? "";
      r = (VERDICT_ORDER[av] ?? -1) - (VERDICT_ORDER[bv] ?? -1);
    } else if (key === "source") {
      const ac = Number(a.payload?.source_count ?? 0);
      const bc = Number(b.payload?.source_count ?? 0);
      r = ac - bc;
      if (r === 0) r = cellString(a, key).localeCompare(cellString(b, key));
    } else if (key === "approved") {
      r = (a.approved ? 1 : 0) - (b.approved ? 1 : 0);
    } else {
      r = cellString(a, key).localeCompare(cellString(b, key), undefined,
                                           { sensitivity: "base", numeric: true });
    }
    return r * mul;
  };
}


function confidenceTooltip(m: AuthorityMatch): string {
  const p = (m.payload ?? {}) as Record<string, unknown>;
  const sources = (p.sources as string[] | undefined) ?? [];
  const guards  = (p.guard_flags as string[] | undefined) ?? [];
  const lines: string[] = [
    `Confidence: ${m.confidence}`,
    `Sources (${sources.length}): ${sources.join(", ") || "none"}`,
    guards.length ? `Guards fired: ${guards.join(", ")}` : "No guards fired",
  ];
  const reasoning = p.reasoning as string | undefined;
  if (reasoning) lines.push("", reasoning);
  return lines.join("\n");
}


function SourcesCell({ match, sourceCount }: { match: AuthorityMatch; sourceCount: number }) {
  const sources = (match.payload?.sources as string[] | undefined) ?? [];
  if (sources.length === 0) {
    return <span className="muted text-xs italic">—</span>;
  }
  return (
    <span className="inline-flex items-center gap-1 flex-wrap"
          title={`Sources: ${sources.join(", ")}${sourceCount >= 2 ? ` · cross-source ×${sourceCount}` : ""}`}>
      {sources.map((s) => (
        <span key={s}
              className="glass-pill px-2 py-[1px] text-[10px] uppercase tracking-wider whitespace-nowrap shrink-0">
          {s}
        </span>
      ))}
      {sourceCount >= 2 && (
        <span className="text-biu-sky text-[10px] shrink-0" title="Cross-source agreement">✓×{sourceCount}</span>
      )}
    </span>
  );
}


function StatusPill({ status }: { status: string }) {
  const tone =
    status === "succeeded" ? "text-biu-sky"
    : status === "running" ? "text-yellow-300"
    : status === "failed"  ? "text-red-300"
    : "muted";
  return <span className={`glass-pill px-3 py-1 text-[10px] kicker ${tone}`}>{status}</span>;
}


