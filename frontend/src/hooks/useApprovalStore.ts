/**
 * useApprovalStore — lightweight polling-based entity store for
 * AI Extraction (web replacement for the desktop's QFileSystemWatcher
 * live sync).
 *
 * Two consumers cooperate:
 *  - The entity table mounts the hook with ``active=false`` so it
 *    polls slowly (default 30 s) — enough to pick up other curators'
 *    approvals without burning CPU.
 *  - The NerVerificationModal flips ``active=true`` while it's open
 *    so the hook polls fast (default 2 s) and the table picks up
 *    fresh AI verdicts within a single revolution after each
 *    ``agent.verdict`` SSE event lands on the server.
 *
 * Polling is cheap: one GET /runs/{id}/extraction/entities per
 * interval, served from the backend's DB-backed view of
 * ``ExtractionApproval``. When the component unmounts the interval
 * is cleared — zero idle cost.
 *
 * TODO(integration): when the table-half agent's
 * ``@/api/extractionApprovals`` lands, replace the local
 * ``Entity`` + ``ExtractionApprovals.list`` shadows here with
 * imports from that file. The runtime shape is the same — the
 * shadows just keep this file type-clean until the sibling lands.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/api/client";
import type { Entity } from "@/api/extractionApprovals";

export type { Entity };


export interface ApprovalStoreState {
  entities:       Entity[];
  total:          number;
  approvedCount:  number;
  loading:        boolean;
  error:          string | null;
  refresh:        () => Promise<void>;
}


export interface UseApprovalStoreOptions {
  /** Fast-poll while true (2 s by default); slow-poll while false
   *  (30 s by default). */
  active?:         boolean;
  /** Active-mode poll interval in ms. */
  pollIntervalMs?: number;
  /** Idle-mode poll interval in ms. */
  idleIntervalMs?: number;
}


/** Backend response shape — list of Entity rows. The endpoint will
 *  also start returning a `total` / `approved` count once the
 *  table-half agent ships, but until then we derive both from
 *  the array length and an in-place reduce. */
async function listEntities(runId: string): Promise<Entity[]> {
  // Defensive: the backend may either return a bare list or an
  // object with `{entities: [...]}`. Accept both. The table-half
  // agent will pin one shape; until then we tolerate both.
  const raw = await api.get<Entity[] | { entities: Entity[] }>(
    `/runs/${runId}/extraction/entities`,
  );
  if (Array.isArray(raw)) return raw;
  if (raw && Array.isArray(raw.entities)) return raw.entities;
  return [];
}


export function useApprovalStore(
  runId: string,
  opts: UseApprovalStoreOptions = {},
): ApprovalStoreState {
  const {
    active         = false,
    pollIntervalMs = 2000,
    idleIntervalMs = 30000,
  } = opts;

  const [entities, setEntities] = useState<Entity[]>([]);
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState<string | null>(null);

  // Suppress overlapping in-flight requests — if the previous poll
  // is still resolving when the next tick fires, skip it.
  const inFlight = useRef(false);
  // Single ref to the timer so a re-render mid-cycle does not
  // accidentally double-start it.
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchOnce = useCallback(async (): Promise<void> => {
    if (!runId) return;
    if (inFlight.current) return;
    inFlight.current = true;
    setLoading(true);
    try {
      const list = await listEntities(runId);
      setEntities(list);
      setError(null);
    } catch (e) {
      setError((e as Error).message || "Failed to load entities");
    } finally {
      setLoading(false);
      inFlight.current = false;
    }
  }, [runId]);

  // Imperative refresh — exposed to callers who want to force an
  // immediate refetch (e.g. straight after a bulk-approve POST).
  const refresh = useCallback(async (): Promise<void> => {
    await fetchOnce();
  }, [fetchOnce]);

  // Drive the polling loop. We use ``setTimeout`` chained from each
  // resolved fetch rather than ``setInterval`` so a slow backend
  // never piles requests on top of each other.
  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    const interval = active ? pollIntervalMs : idleIntervalMs;

    async function tick(): Promise<void> {
      if (cancelled) return;
      await fetchOnce();
      if (cancelled) return;
      timerRef.current = setTimeout(tick, interval);
    }

    // Kick off immediately so the consumer doesn't wait for the
    // first interval before seeing data.
    void tick();

    return () => {
      cancelled = true;
      if (timerRef.current != null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [runId, active, pollIntervalMs, idleIntervalMs, fetchOnce]);

  const approvedCount = entities.reduce(
    (acc, e) => (e.approved ? acc + 1 : acc),
    0,
  );

  return {
    entities,
    total:         entities.length,
    approvedCount,
    loading,
    error,
    refresh,
  };
}
