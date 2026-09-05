import {useCallback, useEffect, useState} from "react";

import {ApiError} from "@/api/client";
import {PublicationApi, type PublicationAuditPage} from "@/api/publication";

export interface WikidataPublicationAuditProps {
  runId: string;
  publicationId: string;
  pageSize?: number;
}

export function WikidataPublicationAudit({
  runId,
  publicationId,
  pageSize = 100,
}: WikidataPublicationAuditProps) {
  const limit = Math.min(200, Math.max(1, pageSize));
  const [cursor, setCursor] = useState<string | null>(null);
  const [history, setHistory] = useState<Array<string | null>>([]);
  const [page, setPage] = useState<PublicationAuditPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void PublicationApi.read(runId, publicationId, {
      type: "audit",
      cursor,
      limit,
    }).then((nextPage) => {
      if (cancelled) return;
      setPage(nextPage);
      setLoading(false);
    }).catch((caught: unknown) => {
      if (cancelled) return;
      setError(caught instanceof ApiError ? caught.detail : String(caught));
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [cursor, limit, publicationId, runId]);

  const next = useCallback(() => {
    if (!page?.next_cursor) return;
    setHistory((current) => [...current, cursor]);
    setCursor(page.next_cursor);
  }, [cursor, page?.next_cursor]);

  const previous = useCallback(() => {
    setHistory((current) => {
      if (current.length === 0) return current;
      setCursor(current[current.length - 1] ?? null);
      return current.slice(0, -1);
    });
  }, []);

  return (
    <section className="space-y-4" data-testid="wikidata-publication-audit">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <div className="kicker">Publication audit</div>
          <h2 className="text-xl font-semibold">Durable publication events</h2>
          <p className="mt-1 text-sm muted" data-testid="publication-audit-total">
            {page ? `${page.total.toLocaleString()} audit event${page.total === 1 ? "" : "s"}` : "Loading audit events…"}
          </p>
        </div>
        <span className="font-mono text-xs muted">{publicationId}</span>
      </div>

      {error && <p className="text-sm text-danger" role="alert">{error}</p>}
      {loading && <p className="text-sm muted" role="status">Loading audit events…</p>}
      {!loading && page?.items.length === 0 && <p className="text-sm muted">No audit events exist.</p>}

      <ol className="space-y-2">
        {page?.items.map((event) => (
          <li key={event.event_id} className="rounded-lg border border-white/10 bg-white/[0.03] p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs font-medium text-biu-sky">#{event.sequence} · {event.event_type}</span>
              <time className="text-xs muted" dateTime={event.occurred_at}>
                {new Date(event.occurred_at).toLocaleString()}
              </time>
            </div>
            <p className="mt-2 text-sm text-ink">{event.message}</p>
            <p className="mt-1 text-xs muted">
              {event.actor_label ?? "System"}
              {event.release_id ? ` · Release ${event.release_id}` : ""}
              {event.entity_id ? ` · Entity ${event.entity_id}` : ""}
            </p>
          </li>
        ))}
      </ol>

      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          className="button-ghost text-sm"
          disabled={loading || history.length === 0}
          onClick={previous}
          data-testid="publication-audit-previous"
        >
          Previous
        </button>
        <button
          type="button"
          className="button-ghost text-sm"
          disabled={loading || !page?.next_cursor}
          onClick={next}
          data-testid="publication-audit-next"
        >
          Next
        </button>
      </div>
    </section>
  );
}
