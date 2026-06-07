/**
 * Co-occurrence panel: shows which works appear together in manuscripts.
 *
 * The server returns a pre-aggregated graph {nodes, edges} so there is no
 * client-side adjacency building or O(W×P) label scan.  The component only
 * does O(nodes) search filtering (fast enough to stay on the browser).
 */
import {useMemo, useState} from "react";
import {researchApi, type CoOccurrenceGraph, type CoOccurrenceNode} from "@/api/research";
import {PanelShell, useAsync} from "./_shared";

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
  const {data, loading, error} = useAsync<CoOccurrenceGraph>(
    () => researchApi.coOccurrence(projectId),
    [projectId],
  );
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  // Build a node-id → node lookup for neighbour resolution
  const nodeById = useMemo(() => {
    if (!data) return new Map<string, CoOccurrenceNode>();
    return new Map(data.nodes.map((n) => [n.id, n]));
  }, [data]);

  // Client-side text search over the pre-sorted node list (O(nodes), fine)
  const filtered = useMemo(() => {
    if (!data) return [];
    const q = query.toLowerCase().trim();
    const nodes = q
      ? data.nodes.filter((n) => n.label.toLowerCase().includes(q))
      : data.nodes;
    return nodes.slice(0, 80);
  }, [data, query]);

  // Edges adjacent to the selected work, sorted by shared_ms_count desc
  const neighbours = useMemo(() => {
    if (!selected || !data) return [];
    return data.edges
      .filter((e) => e.work1 === selected || e.work2 === selected)
      .map((e) => {
        const otherId = e.work1 === selected ? e.work2 : e.work1;
        const other = nodeById.get(otherId);
        return {
          work:            otherId,
          label:           other?.label ?? otherId,
          shared_ms_count: e.shared_ms_count,
          ms_list:         e.ms_list,
        };
      })
      .sort((a, b) => b.shared_ms_count - a.shared_ms_count);
  }, [selected, data, nodeById]);

  const selectedLabel = selected ? (nodeById.get(selected)?.label ?? selected) : null;

  return (
    <PanelShell
      title="Textual Clusters"
      subtitle="Works that appear together in the same manuscripts"
      loading={loading}
      empty={!loading && !data?.nodes.length}
    >
      {error && <p className="text-red-400 text-sm">{error}</p>}
      {data && data.nodes.length > 0 && (
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
                        <td className="py-1.5 tabular-nums text-biu-sky">{n.shared_ms_count}</td>
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
