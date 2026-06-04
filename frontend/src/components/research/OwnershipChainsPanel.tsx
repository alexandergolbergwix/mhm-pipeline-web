/**
 * Ownership Chains — shows per-manuscript owner sequences.
 *
 * Uses a horizontal timeline for each manuscript, with owners as nodes
 * connected by arrows.  Filterable by manuscript label.
 */
import React, {useState, useMemo} from "react";
import {researchApi, type OwnerChain} from "@/api/research";
import {PanelShell, useAsync} from "./_shared";

function ChainRow({chain}: {chain: OwnerChain}) {
  return (
    <div className="border border-white/10 rounded-xl p-4 space-y-2 hover:bg-white/5 transition-colors">
      <p className="text-sm font-medium text-ink truncate" title={chain.ms_label}>
        {chain.ms_label}
      </p>
      <div className="flex flex-wrap items-center gap-1">
        {chain.owners.map((owner, i) => (
          <React.Fragment key={owner.uri}>
            <span
              className="text-xs px-2 py-1 rounded-full bg-white/5 border border-white/10 text-muted hover:border-white/25 transition-colors"
              title={owner.uri}
            >
              {owner.name}
            </span>
            {i < chain.owners.length - 1 && (
              <span className="text-biu-sky/60 text-xs select-none">→</span>
            )}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

export default function OwnershipChainsPanel({projectId}: {projectId: string}) {
  const {data, loading, error} = useAsync<OwnerChain[]>(
    () => researchApi.ownership(projectId),
    [projectId],
  );

  const [query, setQuery] = useState("");
  const [minOwners, setMinOwners] = useState(1);

  const filtered = useMemo(() => {
    if (!data) return [];
    const q = query.toLowerCase();
    return data.filter(
      (c) =>
        c.owners.length >= minOwners &&
        (c.ms_label.toLowerCase().includes(q) ||
          c.owners.some((o) => o.name.toLowerCase().includes(q))),
    );
  }, [data, query, minOwners]);

  return (
    <PanelShell
      title="Ownership Chains"
      subtitle="How manuscripts passed between owners, families, and libraries"
      loading={loading}
      empty={!loading && !data?.length}
    >
      {error && <p className="text-red-400 text-sm">{error}</p>}
      {data && data.length > 0 && (
        <div className="space-y-4">
          <div className="flex gap-3 items-end">
            <div className="flex-1">
              <input
                type="search"
                placeholder="Filter by manuscript or owner…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                className="input-glass w-full text-sm"
              />
            </div>
            <label className="flex flex-col gap-1 text-xs muted whitespace-nowrap">
              Min. owners
              <select
                value={minOwners}
                onChange={(e) => setMinOwners(Number(e.target.value))}
                className="input-glass !py-1 text-sm"
              >
                {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}+</option>)}
              </select>
            </label>
          </div>

          <p className="text-xs muted">
            {filtered.length} manuscript{filtered.length !== 1 ? "s" : ""}
          </p>

          <div className="space-y-2 max-h-[520px] overflow-y-auto pr-1">
            {filtered.map((chain) => (
              <ChainRow key={chain.ms} chain={chain} />
            ))}
          </div>
        </div>
      )}
    </PanelShell>
  );
}
