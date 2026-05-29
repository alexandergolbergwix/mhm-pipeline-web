import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { ApiError } from "@/api/client";
import {
  Runs, type AuthorityMatch, type RunDetail as Detail, type RunMarcRecord,
} from "@/api/runs";
import {
  applyConditions, type FilterCondition, StructuredFilter,
} from "@/components/StructuredFilter";

const COLUMNS = [
  { key: "control_number", label: "Record",     kind: "text" as const },
  { key: "entity_text",    label: "Entity",     kind: "text" as const },
  { key: "role",           label: "Role",       kind: "text" as const },
  { key: "matched_name",   label: "Matched",    kind: "text" as const },
  { key: "confidence",     label: "Confidence", kind: "text" as const },
  { key: "source",         label: "Source",     kind: "text" as const },
  { key: "approved",       label: "Approved",   kind: "boolean" as const },
];

export default function RunDetail() {
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [conditions, setConditions] = useState<FilterCondition[]>([]);
  const [popup, setPopup] = useState<RunMarcRecord | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  async function refresh() {
    if (!runId) return;
    try {
      setRun(await Runs.get(runId));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }
  useEffect(() => { void refresh(); }, [runId]);

  async function toggle(m: AuthorityMatch) {
    if (!runId) return;
    try {
      const updated = await Runs.setApproval(runId, m.id, !m.approved);
      setRun((prev) =>
        prev ? { ...prev, matches: prev.matches.map((x) => x.id === m.id ? updated : x) } : prev,
      );
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

  async function openRecord(cn: string) {
    if (!runId) return;
    try {
      setPopup(await Runs.getRecord(runId, cn));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  const distinct = useMemo<Record<string, string[]>>(() => {
    if (!run) return {};
    const acc: Record<string, Set<string>> = {};
    for (const m of run.matches) {
      for (const c of COLUMNS) {
        acc[c.key] = acc[c.key] ?? new Set();
        acc[c.key].add(String((m as unknown as Record<string, unknown>)[c.key] ?? ""));
      }
    }
    const out: Record<string, string[]> = {};
    for (const k of Object.keys(acc)) out[k] = Array.from(acc[k]).filter(Boolean).sort();
    return out;
  }, [run]);

  const filtered = useMemo(() => {
    if (!run) return [];
    return applyConditions(run.matches, conditions, (m, col) =>
      String((m as unknown as Record<string, unknown>)[col] ?? ""),
    );
  }, [run, conditions]);

  if (error)
    return <Layout><div className="glass p-6 text-red-300">{error}</div></Layout>;
  if (!run)
    return <Layout><p className="muted">Loading run…</p></Layout>;

  const allFilteredSelected = filtered.length > 0 && filtered.every((m) => selected.has(m.id));

  return (
    <Layout>
      <div className="space-y-6">
        <section className="glass p-6 space-y-2">
          <div className="kicker">
            Run · <Link to={`/projects/${run.project_id}`} className="hover:text-ink underline">back to project</Link>
          </div>
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 className="text-2xl font-semibold">{run.name}</h2>
            <StatusPill status={run.status} />
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

          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead className="muted text-left">
                <tr className="border-b border-white/5">
                  <th className="py-2 pr-2">
                    <input type="checkbox" checked={allFilteredSelected}
                           onChange={(e) => {
                             const next = new Set(selected);
                             if (e.target.checked) filtered.forEach((m) => next.add(m.id));
                             else                  filtered.forEach((m) => next.delete(m.id));
                             setSelected(next);
                           }} />
                  </th>
                  {COLUMNS.map((c) => <th key={c.key} className="py-2 pr-3">{c.label}</th>)}
                </tr>
              </thead>
              <tbody>
                {filtered.map((m) => (
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
                    <td className="py-2 pr-3">{m.matched_name || <span className="muted">—</span>}</td>
                    <td className="py-2 pr-3"><ConfidencePill c={m.confidence} /></td>
                    <td className="py-2 pr-3 muted text-xs">{m.source}</td>
                    <td className="py-2 pr-3">
                      <input type="checkbox" checked={m.approved} onChange={() => toggle(m)} />
                    </td>
                  </tr>
                ))}
                {filtered.length === 0 && (
                  <tr><td colSpan={COLUMNS.length + 1} className="py-6 text-center muted">No rows match this filter.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      {popup && <MarcPopup record={popup} onClose={() => setPopup(null)} />}
    </Layout>
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


function ConfidencePill({ c }: { c: string }) {
  const tone =
    c === "high"   ? "text-biu-sky"
    : c === "medium" ? "text-yellow-300"
    : "muted";
  return <span className={`text-xs ${tone}`}>{c}</span>;
}


function MarcPopup({ record, onClose }: { record: RunMarcRecord; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center p-4 bg-black/60" onClick={onClose}>
      <div className="glass max-w-3xl w-full max-h-[80vh] overflow-auto p-6 space-y-3"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-center">
          <div>
            <div className="kicker">MARC record</div>
            <h3 className="font-mono">{record.control_number}</h3>
          </div>
          <button onClick={onClose} className="button-ghost text-sm">Close</button>
        </div>
        <pre className="text-xs font-mono whitespace-pre-wrap"
             style={{ background: "rgba(0,0,0,0.36)", border: "1px solid var(--line)", borderRadius: 12, padding: 12 }}>
          {JSON.stringify(record.marc, null, 2)}
        </pre>
      </div>
    </div>
  );
}
