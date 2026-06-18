/**
 * RelationshipDrillPanel — two tools in one:
 *
 * 1. **Neighbor drill** — enter a URI and see its immediate neighbors
 *    (clickable, so you can hop through the graph).
 *
 * 2. **Path finder** — enter two URIs and find the shortest path between
 *    them (up to 6 hops, bounded by the server).
 *
 * No Cytoscape dependency — the graph is rendered as a simple interactive
 * node list (drill) and a horizontal chain of chips (path), so it works
 * without any additional npm packages.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import {
  researchApi,
  type NeighborNode,
  type PathNode,
  type PathEdge,
  type PathResult,
} from "@/api/research";
import { PanelShell, useAsync } from "./_shared";

// ── shared helpers ─────────────────────────────────────────────────────────

const TYPE_COLOR: Record<string, string> = {
  manuscript: "#a78bfa",
  person:     "#38bdf8",
  place:      "#fb923c",
  work:       "#4ade80",
  entity:     "#94a3b8",
};

function TypeBadge({ type }: { type: string }) {
  const color = TYPE_COLOR[type] ?? TYPE_COLOR.entity;
  return (
    <span
      className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full uppercase tracking-widest"
      style={{ background: `${color}22`, color }}
    >
      {type}
    </span>
  );
}

function UriInput({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="flex items-center gap-2">
      <label className="text-xs text-faint shrink-0 w-12">{label}</label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder ?? "urn:…"}
        className="input-glass flex-1 !py-1.5 text-sm"
      />
    </div>
  );
}

// ── neighbor drill ─────────────────────────────────────────────────────────

function NeighborRow({
  node,
  projectId,
  onDrill,
}: {
  node: NeighborNode;
  projectId: string;
  onDrill: (uri: string) => void;
}) {
  return (
    <tr className="border-b border-white/5 hover:bg-white/5 group">
      <td className="py-1.5 pr-4">
        <TypeBadge type={node.type} />
      </td>
      <td className="py-1.5 pr-4 text-ink font-medium truncate max-w-[12rem]">
        {node.label ?? (
          <span className="text-disabled text-xs">{node.uri.split(/[/#]/).pop()}</span>
        )}
      </td>
      <td className="py-1.5 pr-4">
        <span className="text-xs text-disabled">{node.edge_type}</span>
      </td>
      <td className="py-1.5 pr-2">
        <span className="text-xs text-disabled font-mono truncate max-w-[12rem] block">
          {node.uri}
        </span>
      </td>
      <td className="py-1.5 text-right">
        <div className="flex items-center gap-2 justify-end">
          <button
            onClick={() => onDrill(node.uri)}
            title="Explore neighbors"
            className="text-xs text-disabled hover:text-biu-sky transition-colors"
          >
            ⊕
          </button>
          <Link
            to={`/projects/${projectId}/entity?uri=${encodeURIComponent(node.uri)}`}
            className="text-xs text-disabled hover:text-biu-sky transition-colors"
            title="Open entity page"
          >
            ⓘ
          </Link>
        </div>
      </td>
    </tr>
  );
}

function NeighborDrill({ projectId }: { projectId: string }) {
  const [uri, setUri] = useState("");
  const [history, setHistory] = useState<string[]>([]);

  const { data, loading, error } = useAsync<NeighborNode[]>(
    () => researchApi.neighbors(projectId, uri),
    [uri],
    !uri,
  );

  function drill(nextUri: string) {
    setHistory((h) => [...h, uri]);
    setUri(nextUri);
  }

  function goBack() {
    const prev = history[history.length - 1];
    if (prev !== undefined) {
      setUri(prev);
      setHistory((h) => h.slice(0, -1));
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        {history.length > 0 && (
          <button
            onClick={goBack}
            className="text-xs px-2 py-1 rounded border border-white/10 text-faint hover:text-biu-sky hover:border-biu-sky/50 transition-colors"
          >
            ← back
          </button>
        )}
        <div className="flex-1">
          <UriInput label="URI" value={uri} onChange={setUri} placeholder="urn:hm:MS_… or urn:person:…" />
        </div>
      </div>

      {!uri && (
        <p className="text-sm text-disabled italic">Enter a URI to explore its neighbors.</p>
      )}

      {loading && <p className="text-sm text-disabled">Loading neighbors…</p>}
      {error && <p className="text-sm text-danger">{error}</p>}

      {data && data.length === 0 && uri && (
        <p className="text-sm text-disabled italic">No neighbors found for this URI.</p>
      )}

      {data && data.length > 0 && (
        <div className="overflow-auto max-h-[380px]">
          <table className="w-full text-sm">
            <thead className="sticky top-0 table-head backdrop-blur-sm">
              <tr className="text-left text-xs text-disabled border-b border-white/10">
                <th className="py-2 pr-4 font-medium">Type</th>
                <th className="py-2 pr-4 font-medium">Name</th>
                <th className="py-2 pr-4 font-medium">Relation</th>
                <th className="py-2 pr-2 font-medium">URI</th>
                <th className="py-2 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {data.map((n) => (
                <NeighborRow
                  key={n.uri}
                  node={n}
                  projectId={projectId}
                  onDrill={drill}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── path finder ────────────────────────────────────────────────────────────

function PathChain({
  data,
  projectId,
}: {
  data: PathResult;
  projectId: string;
}) {
  if (data.path.length === 0) {
    return (
      <p className="text-sm text-disabled italic">
        No path found between these two entities (within 6 hops).
      </p>
    );
  }

  return (
    <div className="overflow-x-auto pb-2">
      <div className="flex items-center gap-2 min-w-max">
        {data.path.map((node: PathNode, i: number) => {
          const edge: PathEdge | undefined = data.edges[i];
          const color = TYPE_COLOR[node.type] ?? TYPE_COLOR.entity;
          return (
            <div key={i} className="flex items-center gap-2">
              {/* node chip */}
              <div
                className="shrink-0 rounded-xl border px-3 py-2 text-sm min-w-[8rem] max-w-[12rem]"
                style={{ background: `${color}14`, borderColor: `${color}40` }}
              >
                <TypeBadge type={node.type} />
                <p className="font-medium text-subtle leading-snug truncate mt-1">
                  {node.label ?? (
                    <span className="text-disabled text-xs">{node.uri.split(/[/#]/).pop()}</span>
                  )}
                </p>
                <Link
                  to={`/projects/${projectId}/entity?uri=${encodeURIComponent(node.uri)}`}
                  className="text-[10px] text-disabled hover:text-biu-sky truncate block mt-0.5"
                >
                  ⓘ entity page
                </Link>
              </div>

              {/* edge arrow */}
              {edge && (
                <div className="flex flex-col items-center shrink-0">
                  <span className="text-[10px] text-disabled mb-0.5">{edge.label}</span>
                  <span className="text-disabled">→</span>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function PathFinder({ projectId }: { projectId: string }) {
  const [fromUri, setFromUri] = useState("");
  const [toUri, setToUri]     = useState("");
  const [query, setQuery]     = useState<{ from: string; to: string } | null>(null);

  const { data, loading, error } = useAsync<PathResult>(
    () => researchApi.shortestPath(projectId, query!.from, query!.to),
    [query?.from, query?.to],
    !query,
  );

  function handleFind() {
    if (fromUri && toUri) setQuery({ from: fromUri, to: toUri });
  }

  return (
    <div className="space-y-3">
      <div className="space-y-2">
        <UriInput label="From" value={fromUri} onChange={setFromUri} />
        <UriInput label="To"   value={toUri}   onChange={setToUri}   />
      </div>
      <button
        onClick={handleFind}
        disabled={!fromUri || !toUri}
        className="text-xs px-4 py-1.5 rounded-lg border border-biu-sky/40 text-biu-sky hover:bg-biu-sky/10 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
      >
        Find path
      </button>

      {!query && (
        <p className="text-sm text-disabled italic">
          Enter two URIs to find the shortest relationship path between them.
        </p>
      )}
      {loading && <p className="text-sm text-disabled">Finding path…</p>}
      {error && <p className="text-sm text-danger">{error}</p>}
      {data && <PathChain data={data} projectId={projectId} />}
    </div>
  );
}

// ── panel ─────────────────────────────────────────────────────────────────

export default function RelationshipDrillPanel({
  projectId,
}: {
  projectId: string;
}) {
  const [mode, setMode] = useState<"drill" | "path">("drill");

  return (
    <PanelShell
      title="Relationship Explorer"
      subtitle="Drill into entity neighbors or find paths between any two entities"
    >
      <div className="flex gap-2 mb-4">
        {(["drill", "path"] as const).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`text-xs px-3 py-1.5 rounded-full border font-medium transition-colors ${
              mode === m
                ? "bg-biu-sky/20 border-biu-sky text-biu-sky"
                : "border-white/10 text-muted hover:border-white/30"
            }`}
          >
            {m === "drill" ? "Neighbor drill" : "Path finder"}
          </button>
        ))}
      </div>

      {mode === "drill" && <NeighborDrill projectId={projectId} />}
      {mode === "path"  && <PathFinder   projectId={projectId} />}
    </PanelShell>
  );
}
