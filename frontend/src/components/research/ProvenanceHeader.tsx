import {Link} from "react-router-dom";
import {researchApi, type ResearchSummary} from "@/api/research";
import {useAsync} from "./_shared";

export function ProvenanceHeader({
  projectId,
  runId,
}: {
  projectId: string;
  runId: string;
}) {
  const {data, loading} = useAsync<ResearchSummary>(
    () => researchApi.summary(projectId),
    [projectId],
  );

  return (
    <div className="glass p-4 rounded-xl border border-biu-sky/20 bg-biu-sky/5 space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="text-biu-sky text-lg">🔗</span>
          <span className="text-sm font-semibold text-ink">Linked Data Explorer</span>
          <span className="text-xs muted">· merged across RDF, Wikibase &amp; Wikidata</span>
        </div>
        <Link
          to={`/runs/${runId}/wikidata-studio`}
          className="text-xs text-biu-sky hover:underline"
        >
          ← Wikidata Studio
        </Link>
      </div>

      {loading ? (
        <div className="flex gap-4 animate-pulse">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-4 w-20 rounded bg-white/10" />
          ))}
        </div>
      ) : data ? (
        <div className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
          <Stat n={data.total_manuscripts} label="manuscripts" />
          <Stat n={data.total_works} label="works" />
          <Stat n={data.total_persons} label="persons" />
          <Stat n={data.total_places} label="places" />
          <span className="muted text-xs self-center">
            · {data.triples.toLocaleString()} triples
          </span>
        </div>
      ) : null}
    </div>
  );
}

function Stat({n, label}: {n: number; label: string}) {
  return (
    <span className="text-ink font-medium">
      {n.toLocaleString()}{" "}
      <span className="muted font-normal">{label}</span>
    </span>
  );
}
