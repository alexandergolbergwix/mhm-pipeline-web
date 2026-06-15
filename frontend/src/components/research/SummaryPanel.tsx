import {researchApi, type EntityKind, type ResearchSummary} from "@/api/research";
import {PanelShell, useAsync} from "./_shared";

const ROLE_COLORS: Record<string, string> = {
  scribe: "#38bdf8",
  author: "#a78bfa",
  owner:  "#34d399",
};

const SOURCE_LABELS: Record<"rdf" | "wikibase" | "wikidata", string> = {
  rdf: "RDF",
  wikibase: "Wikibase",
  wikidata: "Wikidata",
};

/** A stat pill showing the merged total plus a per-source breakdown. Falls
 *  back to the legacy single number when ``by_type`` is absent. */
function SourceStatPill({
  label,
  kind,
  data,
}: {
  label: string;
  kind: EntityKind;
  data: ResearchSummary;
}) {
  const agg = data.by_type?.[kind];
  const legacy: Record<EntityKind, number> = {
    manuscript: data.total_manuscripts,
    work: data.total_works,
    person: data.total_persons,
    place: data.total_places,
  };
  const total = agg?.total ?? legacy[kind];
  const available = data.sources_available;

  return (
    <div className="flex flex-col items-center gap-1.5 px-4 py-3 rounded-xl bg-white/5 border border-white/10">
      <span className="text-2xl font-bold text-biu-sky tabular-nums">{total.toLocaleString()}</span>
      <span className="text-xs muted">{label}</span>
      {agg && (
        <div className="flex flex-wrap justify-center gap-x-2 gap-y-0.5 text-[10px] tabular-nums">
          {(["rdf", "wikidata", "wikibase"] as const).map((s) => {
            const off = available ? !available[s] : false;
            return (
              <span key={s} className={off ? "opacity-30" : "muted"}>
                {SOURCE_LABELS[s]} {off ? "—" : agg.by_source[s].toLocaleString()}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

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
      subtitle="Merged across RDF graph, Wikibase, and Wikidata"
      loading={loading}
      empty={!loading && !data && !error}
    >
      {error && <p className="text-red-400 text-sm">{error}</p>}
      {data && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <SourceStatPill label="Manuscripts" kind="manuscript" data={data} />
            <SourceStatPill label="Works"       kind="work"       data={data} />
            <SourceStatPill label="Persons"     kind="person"     data={data} />
            <SourceStatPill label="Places"      kind="place"      data={data} />
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
