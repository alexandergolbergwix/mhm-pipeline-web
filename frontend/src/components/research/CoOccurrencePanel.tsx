/**
 * Co-occurrence panel: shows which works appear together in manuscripts.
 *
 * Builds an adjacency list from the pair list, then lets the user search
 * for a particular work and see all works that share a manuscript with it.
 */
import {useMemo, useState} from "react";
import {researchApi, type CoOccurrencePair} from "@/api/research";
import {PanelShell, useAsync} from "./_shared";

interface AdjEntry {
  work: string;
  label: string;
  mss: Set<string>;
}

function buildAdjacency(pairs: CoOccurrencePair[]): Map<string, Map<string, AdjEntry>> {
  const adj = new Map<string, Map<string, AdjEntry>>();

  const add = (from: string, _fromLabel: string, to: string, toLabel: string, ms: string) => {
    if (!adj.has(from)) adj.set(from, new Map());
    const nbrs = adj.get(from)!;
    if (!nbrs.has(to)) nbrs.set(to, {work: to, label: toLabel, mss: new Set()});
    nbrs.get(to)!.mss.add(ms);
  };

  for (const p of pairs) {
    add(p.work1, p.work1_label, p.work2, p.work2_label, p.ms);
    add(p.work2, p.work2_label, p.work1, p.work1_label, p.ms);
  }
  return adj;
}

function WorkBadge({label, count, onClick, selected}: {
  label: string;
  count: number;
  onClick: () => void;
  selected: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={`text-xs px-2.5 py-1 rounded-full border transition-colors text-left ${
        selected
          ? "bg-biu-sky/20 border-biu-sky text-biu-sky"
          : "border-white/10 text-muted hover:border-white/30"
      }`}
    >
      {label}
      {count > 1 && <span className="ml-1 opacity-60">×{count}</span>}
    </button>
  );
}

export default function CoOccurrencePanel({projectId}: {projectId: string}) {
  const {data, loading, error} = useAsync<CoOccurrencePair[]>(
    () => researchApi.coOccurrence(projectId),
    [projectId],
  );
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const adj = useMemo(() => (data ? buildAdjacency(data) : new Map()), [data]);

  // Build a sorted list of all unique works by their degree (distinct co-works)
  const allWorks = useMemo(() => {
    const entries: {id: string; label: string; degree: number}[] = [];
    adj.forEach((nbrs, wid) => {
      const label = data?.find((p) => p.work1 === wid)?.work1_label
        ?? data?.find((p) => p.work2 === wid)?.work2_label
        ?? wid;
      entries.push({id: wid, label, degree: nbrs.size});
    });
    return entries.sort((a, b) => b.degree - a.degree);
  }, [adj, data]);

  const filtered = useMemo(() => {
    if (!query.trim()) return allWorks.slice(0, 80);
    const q = query.toLowerCase();
    return allWorks.filter((w) => w.label.toLowerCase().includes(q)).slice(0, 80);
  }, [allWorks, query]);

  const neighbours = useMemo(() => {
    if (!selected) return [];
    const nbrs = adj.get(selected);
    if (!nbrs) return [];
    return [...nbrs.values()].sort((a, b) => b.mss.size - a.mss.size);
  }, [selected, adj]);

  const selectedLabel = selected
    ? (allWorks.find((w) => w.id === selected)?.label ?? selected)
    : null;

  return (
    <PanelShell
      title="Textual Clusters"
      subtitle="Works that appear together in the same manuscripts"
      loading={loading}
      empty={!loading && !data?.length}
    >
      {error && <p className="text-red-400 text-sm">{error}</p>}
      {data && data.length > 0 && (
        <div className="space-y-4">
          <input
            type="search"
            placeholder="Search works…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="input-glass w-full text-sm"
          />
          <div className="flex flex-wrap gap-2 max-h-48 overflow-y-auto">
            {filtered.map((w) => (
              <WorkBadge
                key={w.id}
                label={w.label}
                count={w.degree}
                selected={selected === w.id}
                onClick={() => setSelected(selected === w.id ? null : w.id)}
              />
            ))}
          </div>

          {selected && (
            <div className="border-t border-white/10 pt-4 space-y-2">
              <p className="text-sm font-medium text-ink">
                Works co-occurring with{" "}
                <span className="text-biu-sky">{selectedLabel}</span>
              </p>
              {neighbours.length === 0 ? (
                <p className="muted text-sm">No co-occurrences found.</p>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs muted border-b border-white/10">
                      <th className="py-1 pr-4 font-medium">Work</th>
                      <th className="py-1 font-medium">Shared manuscripts</th>
                    </tr>
                  </thead>
                  <tbody>
                    {neighbours.map((n) => (
                      <tr
                        key={n.work}
                        className="border-b border-white/5 hover:bg-white/5 cursor-pointer"
                        onClick={() => setSelected(n.work)}
                      >
                        <td className="py-1.5 pr-4 text-ink">{n.label}</td>
                        <td className="py-1.5 tabular-nums text-biu-sky">{n.mss.size}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </div>
      )}
    </PanelShell>
  );
}
