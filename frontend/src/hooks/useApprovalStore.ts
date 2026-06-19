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
 * A localStorage tier (``@/cache/extractionCache``) serves fresh
 * entity lists instantly on mount; network polls still run but skip
 * state updates when the payload fingerprint is unchanged.
 */

import {useCallback, useEffect, useRef, useState} from "react";

import {api} from "@/api/client";
import type {Entity} from "@/api/extractionApprovals";
import {
  EXTRACTION_ENTITIES_REFRESH_EVENT,
  getEntitiesCache,
  setEntitiesCache,
} from "@/cache/extractionCache";

export type {Entity};


export interface ApprovalStoreState {
  entities:       Entity[];
  total:          number;
  approvedCount:  number;
  /** Distinct control numbers across the full run (server aggregate). */
  recordCount:    number;
  /** Per-source entity counts across the full run (server aggregate),
   *  e.g. {person_ner: 40, provenance_ner: 12, ...}. */
  sourceCounts:   Record<string, number>;
  loading:        boolean;
  error:          string | null;
  refresh:        () => Promise<void>;
}


interface EntitiesPayload {
  entities:       Entity[];
  total:          number;
  approvedCount:  number;
  recordCount:    number;
  sourceCounts:   Record<string, number>;
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


interface RawEntitiesResponse {
  entities?:       Entity[];
  total?:          number;
  approved_count?: number;
  record_count?:   number;
  source_counts?:  Record<string, number>;
}

function entitiesFingerprint(entities: Entity[]): string {
  return entities.map((e) => [
    e.id,
    e.approved,
    e.override_text ?? "",
    e.override_type ?? "",
    e.override_role ?? "",
    e.ai_verdict?.overall ?? "",
    e.ai_verdict?.cache_key ?? "",
  ].join("|")).join("\n");
}

/** Fetch the run's entities + run-level aggregates. The backend returns
 *  `{entities, total, approved_count, record_count, source_counts}`; we
 *  still tolerate a bare list (older mocks) and derive the aggregates
 *  from the array in that case. */
async function listEntities(runId: string): Promise<EntitiesPayload> {
  const raw = await api.get<Entity[] | RawEntitiesResponse>(
    `/runs/${runId}/extraction/entities`,
  );
  const entities = Array.isArray(raw) ? raw : (raw.entities ?? []);
  const obj = Array.isArray(raw) ? null : raw;
  const approvedCount = obj?.approved_count
    ?? entities.reduce((acc, e) => (e.approved ? acc + 1 : acc), 0);
  const sourceCounts = obj?.source_counts ?? deriveSourceCounts(entities);
  const recordCount = obj?.record_count
    ?? new Set(entities.map((e) => e.control_number)).size;
  return {
    entities,
    total: obj?.total ?? entities.length,
    approvedCount,
    recordCount,
    sourceCounts,
  };
}

function deriveSourceCounts(entities: Entity[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const e of entities) out[e.source] = (out[e.source] ?? 0) + 1;
  return out;
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
  const [meta, setMeta] = useState<Omit<EntitiesPayload, "entities">>({
    total: 0, approvedCount: 0, recordCount: 0, sourceCounts: {},
  });
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState<string | null>(null);

  const inFlight = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastFingerprint = useRef<string>("");

  const applyPayload = useCallback((payload: EntitiesPayload): void => {
    const fp = entitiesFingerprint(payload.entities);
    if (fp === lastFingerprint.current && entities.length > 0) return;
    lastFingerprint.current = fp;
    setEntities(payload.entities);
    setMeta({
      total: payload.total,
      approvedCount: payload.approvedCount,
      recordCount: payload.recordCount,
      sourceCounts: payload.sourceCounts,
    });
  }, [entities.length]);

  const fetchOnce = useCallback(async (force = false): Promise<void> => {
    if (!runId) return;
    if (inFlight.current) return;
    inFlight.current = true;
    if (!force && entities.length === 0) {
      const cached = getEntitiesCache(runId, active);
      if (cached) {
        applyPayload(cached);
      }
    }
    setLoading(true);
    try {
      const payload = await listEntities(runId);
      setEntitiesCache(runId, payload);
      applyPayload(payload);
      setError(null);
    } catch (e) {
      setError((e as Error).message || "Failed to load entities");
    } finally {
      setLoading(false);
      inFlight.current = false;
    }
  }, [runId, active, entities.length, applyPayload]);

  const refresh = useCallback(async (): Promise<void> => {
    lastFingerprint.current = "";
    await fetchOnce(true);
  }, [fetchOnce]);

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

    void tick();

    return () => {
      cancelled = true;
      if (timerRef.current != null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [runId, active, pollIntervalMs, idleIntervalMs, fetchOnce]);

  useEffect(() => {
    if (!runId || typeof window === "undefined") return;
    function onRefreshed(ev: Event): void {
      const detail = (ev as CustomEvent<{runId?: string}>).detail;
      if (detail?.runId && detail.runId !== runId) return;
      lastFingerprint.current = "";
      void fetchOnce(true);
    }
    window.addEventListener(EXTRACTION_ENTITIES_REFRESH_EVENT, onRefreshed);
    return () => {
      window.removeEventListener(EXTRACTION_ENTITIES_REFRESH_EVENT, onRefreshed);
    };
  }, [runId, fetchOnce]);

  return {
    entities,
    total:         meta.total,
    approvedCount: meta.approvedCount,
    recordCount:   meta.recordCount,
    sourceCounts:  meta.sourceCounts,
    loading,
    error,
    refresh,
  };
}
