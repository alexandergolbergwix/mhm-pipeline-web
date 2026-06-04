import {researchApi, ResearchSummary} from "@/api/research";
import {PanelShell, StatPill, useAsync} from "./_shared";

const ROLE_COLOR: Record<string, string> = {
  scribe: "#38bdf8",
  author: "#a78bfa",
  owner:  "#34d399",
};

function BarChart({
  bars,
}: {
  bars: {label: string; value: number; color: string}[];
}) {
  const max = Math.max(...bars.map((b) => b.value), 1);
  return (
    <div className="space-y-2">
      {bars.map((b) => (
        <div key={b.label} className="flex items-center gap-3 text-sm">
          <span className="w-16 text-right text-xs muted">{b.label}</span>
          <div className="flex-1 h-5 rounded bg-white/5 overflow-hidden">
            <div
              className="h-full rounded transition-all duration-500"
              style={{width: `${(b.value / max) * 100}%`, background: b.color}}
            />
          </div>
          <span className="w-8 text-xs tabular-nums text-right" style={{color: b.color}}>
            {b.value}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function SummaryPanel({projectId}: {projectId: string}) {
  const {data, loading, error} = useAsync<ResearchSummary>(
    () => researchApi.summary(projectId),
    [projectId],
  );

  const bars = data
    ? [
        {label: "Scribes", value: data.persons_by_role.scribe, color: ROLE_COLOR.scribe},
        {label: "Authors", value: data.persons_by_role.author, color: ROLE_COLOR.author},
        {label: "Owners",  value: data.persons_by_role.owner,  color: ROLE_COLOR.owner},
      ]
    : [];

  return (
    <PanelShell
      title="Project Overview"
      subtitle="Aggregate statistics across all runs in this project"
      loading={loading}
      empty={!loading && !data && !error}
    >
      {error && <p className="text-red-400 text-sm">{error}</p>}
      {data && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <StatPill label="Manuscripts" value={data.total_manuscripts} />
            <StatPill label="Works"       value={data.total_works} />
            <StatPill label="Persons"     value={data.total_persons} />
            <StatPill label="Places"      value={data.total_places} />
          </div>
          <div>
            <p className="text-xs muted mb-3 font-medium uppercase tracking-wide">People by role</p>
            <BarChart bars={bars} />
          </div>
          <p className="text-xs muted text-right">{data.triples.toLocaleString()} triples in merged graph</p>
        </div>
      )}
    </PanelShell>
  );
}
