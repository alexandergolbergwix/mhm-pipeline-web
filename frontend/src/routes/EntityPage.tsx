/**
 * EntityPage — resolves a Linked-Data URI to its full entity summary.
 *
 * Route: /projects/:projectId/entity?uri=<encoded_uri>
 *
 * Displays: label, type badge, roles, manuscript list with roles,
 * geographic coordinates (places), authority identifiers, and
 * birth/death dates (persons).
 */

import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { entityApi, type EntityDetailResponse } from "@/api/entities";

// ── type badge ─────────────────────────────────────────────────────────────

const TYPE_COLOURS: Record<string, string> = {
  person:     "bg-blue-100 text-blue-800",
  place:      "bg-green-100 text-green-800",
  work:       "bg-violet-100 text-violet-800",
  manuscript: "bg-amber-100 text-amber-800",
};

function TypeBadge({ type }: { type: string }) {
  const cls = TYPE_COLOURS[type] ?? "bg-slate-100 text-slate-600";
  return (
    <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold ${cls}`}>
      {type}
    </span>
  );
}

// ── identifier links ───────────────────────────────────────────────────────

const IDENTIFIER_URL: Record<string, (id: string) => string> = {
  viaf:     (id) => `https://viaf.org/viaf/${id}`,
  wikidata: (id) => `https://www.wikidata.org/wiki/${id}`,
};

function IdentifierLinks({ identifiers }: { identifiers: Record<string, string> }) {
  const entries = Object.entries(identifiers);
  if (entries.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {entries.map(([key, val]) => {
        const href = IDENTIFIER_URL[key]?.(val);
        return href ? (
          <a
            key={key}
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 rounded border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs text-blue-600 hover:bg-slate-100 hover:underline"
          >
            <span className="font-medium uppercase text-slate-500">{key}</span>
            <span>{val}</span>
          </a>
        ) : (
          <span
            key={key}
            className="inline-flex items-center gap-1 rounded border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs text-slate-700"
          >
            <span className="font-medium uppercase text-slate-400">{key}</span>
            <span>{val}</span>
          </span>
        );
      })}
    </div>
  );
}

// ── manuscript row ─────────────────────────────────────────────────────────

function ManuscriptRow({
  ms,
  projectId,
}: {
  ms: EntityDetailResponse["manuscripts"][0];
  projectId: string;
}) {
  const evidenceUrl = `/projects/${projectId}/entity?uri=${encodeURIComponent(ms.uri)}`;
  return (
    <li className="flex items-center justify-between gap-4 py-2 border-b border-slate-100 last:border-0">
      <div className="min-w-0">
        <span className="block text-sm font-medium text-slate-800 truncate">
          {ms.label ?? ms.uri}
        </span>
        <span className="text-xs text-slate-500 font-mono">{ms.uri}</span>
      </div>
      <div className="flex items-center gap-3 shrink-0">
        <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
          {ms.role}
        </span>
        <Link
          to={evidenceUrl}
          className="text-xs text-blue-600 hover:underline"
          title="View entity detail"
        >
          View →
        </Link>
      </div>
    </li>
  );
}

// ── main page ──────────────────────────────────────────────────────────────

export default function EntityPage() {
  const { projectId = "" } = useParams<{ projectId: string }>();
  const [searchParams] = useSearchParams();
  const uri = searchParams.get("uri") ?? "";

  const [data, setData] = useState<EntityDetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!uri || !projectId) return;
    setLoading(true);
    setError(null);
    entityApi
      .getEntityDetail(projectId, uri)
      .then(setData)
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : String(err))
      )
      .finally(() => setLoading(false));
  }, [projectId, uri]);

  if (!uri) {
    return (
      <div className="p-8 text-slate-500 text-sm">No entity URI provided.</div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      {/* back link */}
      <nav className="mb-6 text-xs text-slate-400">
        <Link to={`/projects/${projectId}`} className="hover:underline">
          ← Project
        </Link>
      </nav>

      {loading && (
        <p className="text-slate-400 text-sm">Loading entity…</p>
      )}

      {error && (
        <div className="rounded-md bg-red-50 border border-red-200 p-4 text-sm text-red-700">
          {error}
        </div>
      )}

      {data && (
        <div className="space-y-6">
          {/* header */}
          <div className="flex items-start gap-3">
            <TypeBadge type={data.type} />
            <div className="min-w-0">
              <h1 className="text-2xl font-bold text-slate-900 leading-tight">
                {data.label ?? uri}
              </h1>
              <p className="mt-1 font-mono text-xs text-slate-400 break-all">{uri}</p>
            </div>
          </div>

          {/* dates (persons) */}
          {data.dates && (
            <section>
              <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-1">
                Dates
              </h2>
              <p className="text-sm text-slate-700">
                {data.dates.birth && <>b. {data.dates.birth}</>}
                {data.dates.birth && data.dates.death && " — "}
                {data.dates.death && <>d. {data.dates.death}</>}
              </p>
            </section>
          )}

          {/* geo (places) */}
          {data.geo && (
            <section>
              <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-1">
                Coordinates
              </h2>
              <p className="text-sm text-slate-700">
                {data.geo.lat.toFixed(4)}, {data.geo.lon.toFixed(4)}
              </p>
            </section>
          )}

          {/* roles */}
          {data.roles.length > 0 && (
            <section>
              <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
                Roles
              </h2>
              <div className="flex flex-wrap gap-2">
                {data.roles.map((r) => (
                  <span
                    key={r}
                    className="rounded bg-slate-100 px-2.5 py-0.5 text-sm text-slate-700"
                  >
                    {r}
                  </span>
                ))}
              </div>
            </section>
          )}

          {/* external identifiers */}
          {Object.keys(data.identifiers).length > 0 && (
            <section>
              <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
                External identifiers
              </h2>
              <IdentifierLinks identifiers={data.identifiers} />
            </section>
          )}

          {/* manuscripts */}
          <section>
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
              Manuscripts ({data.manuscripts.length})
            </h2>
            {data.manuscripts.length === 0 ? (
              <p className="text-sm text-slate-400 italic">
                No manuscripts associated with this entity.
              </p>
            ) : (
              <ul className="divide-y divide-slate-100">
                {data.manuscripts.map((ms) => (
                  <ManuscriptRow key={`${ms.uri}:${ms.role}`} ms={ms} projectId={projectId} />
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
