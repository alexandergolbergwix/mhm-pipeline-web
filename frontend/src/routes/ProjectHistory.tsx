import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { ApiError } from "@/api/client";
import { History, type ProjectEvent, type Snapshot } from "@/api/history";
import { useProjectEvents } from "@/api/realtime";
import {Glass} from "@/components/glass";

const FRIENDLY: Record<string, string> = {
  "project.created":      "Created project",
  "project.updated":      "Updated project",
  "member.added":         "Added member",
  "member.removed":       "Removed member",
  "member.role_changed":  "Changed member role",
  "run.created":          "Uploaded a run",
  "match.approved":       "Approved a match",
  "match.unapproved":     "Unapproved a match",
  "match.bulk_approved":  "Bulk approved/unapproved",
  "snapshot.tagged":      "Tagged snapshot",
  "snapshot.restored":    "Restored from snapshot",
};

const PAGE_SIZE = 50;

export default function ProjectHistory() {
  const { projectId } = useParams<{ projectId: string }>();
  const [events, setEvents] = useState<ProjectEvent[] | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busyEvent, setBusyEvent] = useState<string | null>(null);
  // Cursor for "Load more" — the oldest event's created_at on the current page.
  const [beforeCursor, setBeforeCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  async function fetchPage(before?: string, append = false) {
    if (!projectId) return;
    try {
      const page = await History.events(projectId, { limit: PAGE_SIZE, before });
      setHasMore(page.length === PAGE_SIZE);
      if (append) {
        setEvents((prev) => [...(prev ?? []), ...page]);
      } else {
        setEvents(page);
      }
      if (page.length > 0) {
        setBeforeCursor(page[page.length - 1].created_at);
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function refresh() {
    if (!projectId) return;
    try {
      // Reset to first page on refresh.
      setBeforeCursor(null);
      await fetchPage(undefined, false);
      setSnapshots(await History.snapshots(projectId));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  useEffect(() => { void refresh(); }, [projectId]);

  useProjectEvents(projectId, () => { void refresh(); }, {onReconnect: () => void refresh()});

  async function loadMore() {
    if (!beforeCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      await fetchPage(beforeCursor, true);
    } finally {
      setLoadingMore(false);
    }
  }

  if (!projectId) return null;

  async function tag(e: ProjectEvent) {
    const name = prompt("Snapshot name:");
    if (!name) return;
    setBusyEvent(e.id);
    try {
      await History.tagSnapshot(projectId!, e.id, name);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally { setBusyEvent(null); }
  }

  async function restore(e: ProjectEvent) {
    if (!confirm(`Restore approvals to the state at ${new Date(e.created_at).toLocaleString()}?`)) return;
    setBusyEvent(e.id);
    try {
      const r = await History.restoreTo(projectId!, e.id);
      alert(`Restored. ${r.matches_changed} match(es) updated.`);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally { setBusyEvent(null); }
  }

  return (
    <Layout>
      <div className="space-y-6">
        <Glass as="section" className="p-6">
          <div className="kicker">
            History · <Link to={`/projects/${projectId}`} className="hover:text-ink underline">back to project</Link>
          </div>
          <h2 className="text-2xl font-semibold mt-1">Git-styled history</h2>
          <p className="muted text-sm mt-2 max-w-2xl">
            Every approval, member change, run upload, and snapshot is a
            row in an append-only event log. <b className="text-ink">Restore to here</b>{" "}
            rewinds your approvals to the state at any earlier event.
          </p>
        </Glass>

        {error && <Glass className="p-4 text-danger text-sm">{error}</Glass>}

        {snapshots.length > 0 && (
          <Glass as="section" className="p-6 space-y-2">
            <div className="kicker">Named snapshots</div>
            <ul className="space-y-2">
              {snapshots.map((s) => (
                <li key={s.id} className="flex items-center justify-between text-sm">
                  <div>
                    <b>{s.name}</b>
                    <span className="muted ml-2">at {new Date(s.created_at).toLocaleString()}</span>
                  </div>
                  <button
                    onClick={async () => {
                      if (!confirm(`Restore to snapshot "${s.name}"?`)) return;
                      const r = await History.restoreTo(projectId!, s.event_id);
                      alert(`Restored. ${r.matches_changed} match(es) updated.`);
                      await refresh();
                    }}
                    className="button-ghost text-xs">
                    Restore to here
                  </button>
                </li>
              ))}
            </ul>
          </Glass>
        )}

        <Glass as="section" className="p-6 space-y-3">
          <h3 className="text-lg font-medium">Timeline</h3>
          {events === null && <p className="muted">Loading…</p>}
          {events && events.length === 0 && <p className="muted italic">No events yet.</p>}
          <ol className="relative pl-4 border-l border-white/10 space-y-4">
            {events?.map((e) => (
              <li key={e.id} className="relative">
                <span className="absolute -left-[7px] top-2 w-3 h-3 rounded-full bg-biu-sky/80 shadow-[0_0_10px_rgba(119,204,229,0.6)]" />
                <div className="flex items-baseline justify-between gap-4 flex-wrap">
                  <div>
                    <p className="text-sm">
                      <b className="text-ink">{FRIENDLY[e.type] ?? e.type}</b>
                      {e.actor_name && <span className="muted"> · by {e.actor_name}</span>}
                    </p>
                    <p className="text-xs muted">{new Date(e.created_at).toLocaleString()}</p>
                    {Object.keys(e.payload).length > 0 && (
                      <details className="text-xs muted mt-1">
                        <summary className="cursor-pointer hover:text-ink">payload</summary>
                        <pre className="mt-1 p-2 rounded code-surface">
                          {JSON.stringify(e.payload, null, 2)}
                        </pre>
                      </details>
                    )}
                  </div>
                  <div className="flex gap-2 shrink-0">
                    <button onClick={() => tag(e)} disabled={busyEvent === e.id}
                            className="button-ghost text-xs">Tag snapshot</button>
                    <button onClick={() => restore(e)} disabled={busyEvent === e.id}
                            className="button-ghost text-xs">Restore to here</button>
                  </div>
                </div>
              </li>
            ))}
          </ol>
          {hasMore && (
            <div className="pt-2 text-center">
              <button
                onClick={() => void loadMore()}
                disabled={loadingMore}
                className="button-ghost text-xs"
                data-testid="load-more-events"
              >
                {loadingMore ? "Loading…" : "Load more"}
              </button>
            </div>
          )}
        </Glass>
      </div>
    </Layout>
  );
}
