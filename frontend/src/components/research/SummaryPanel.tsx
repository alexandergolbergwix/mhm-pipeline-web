import {researchApi, type ResearchSummary} from "@/api/research";
import {PanelShell, StatPill, useAsync} from "./_shared";

const ROLE_COLORS: Record<string, string> = {
  scribe: "#38bdf8",
  author: "#a78bfa",
  owner:  "#34d399",
};

export default function SummaryPanel({projectId}: {projectId: string}) {
  const {data, loading, error} = useAsync<ResearchSummary>(
    () => researchApi.summary(projectId),
    [projectId],
  );

  const roleData = data
    ? [
        {name: "Scribes", value: data.persons_by_role.scribe, key: "scribe"},
        {name: "Authors", value: data.persons_by_role.author, key: "author"},
        {name: "Owners",  value: data.persons_by_role.owner,  key: "owner"},
      ]
    : [];

  const maxRole = roleData.reduce((m, d) => Math.max(m, d.value), 0);

  return (
    <PanelShell
      title="Project Overview"
      subtitle="Aggregate statistics across all runs"
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

          {/* CSS bar chart for persons by role */}
          <div>
            <p className="text-xs muted mb-3 font-medium uppercase tracking-wide">People by role</p>
            <div className="space-y-2">
              {roleData.map((d) => (
                <div key={d.key} className="flex items-center gap-3">
                  <span className="text-xs muted w-14 text-right">{d.name}</span>
                  <div className="flex-1 h-6 rounded-sm bg-white/5 overflow-hidden">
                    <div
                      className="h-full rounded-sm transition-all duration-500"
                      style={{
                        width: maxRole > 0 ? `${(d.value / maxRole) * 100}%` : "0%",
                        background: ROLE_COLORS[d.key] ?? "#60a5fa",
                        opacity: 0.85,
                      }}
                    />
                  </div>
                  <span className="text-xs font-medium tabular-nums w-10">{d.value.toLocaleString()}</span>
                </div>
              ))}
            </div>
          </div>

          <p className="text-xs muted text-right">{data.triples.toLocaleString()} triples in merged graph</p>
        </div>
      )}
    </PanelShell>
  );
}
