/**
 * useApprovalStore — lightweight polling-based entity store for
 * AI Extraction (web replacement for the desktop's QFileSystemWatcher
 * live sync).
 *
 * Tier 3 browser cache: user-scoped SWR via ``@/cache/extractionCache``.
 * Always revalidates against the API; never blocks on a partial list.
 */

import {useCallback, useEffect, useRef, useState} from "react";

import {ApiError} from "@/api/client";
import type {Entity} from "@/api/extractionApprovals";
import {
  EXTRACTION_ENTITIES_REFRESH_EVENT,
  getEntitiesCache,
  setEntitiesCache,
} from "@/cache/extractionCache";
import {useAuth} from "@/stores/auth";

export type {Entity};


export interface ApprovalStoreState {
  entities:       Entity[];
  total:          number;
  approvedCount:  number;
  recordCount:    number;
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
  etag?:          string | null;
}


export interface UseApprovalStoreOptions {
  active?:         boolean;
  pollIntervalMs?: number;
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
  return `${entities.length}:${entities.map((e) => [
    e.id,
    e.approved,
    e.override_text ?? "",
    e.override_type ?? "",
    e.override_role ?? "",
    e.ai_verdict?.overall ?? "",
    e.ai_verdict?.cache_key ?? "",
  ].join("|")).join("\n")}`;
}

function normalizePayload(
  raw: Entity[] | RawEntitiesResponse,
  etag: string | null,
): EntitiesPayload {
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
    etag,
  };
}

function deriveSourceCounts(entities: Entity[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const e of entities) out[e.source] = (out[e.source] ?? 0) + 1;
  return out;
}

async function fetchEntitiesFromApi(
  runId: string,
  ifNoneMatch?: string | null,
): Promise<{notModified: boolean; payload: EntitiesPayload | null}> {
  const headers: Record<string, string> = {
    "Cache-Control": "no-cache",
    "Pragma":        "no-cache",
  };
  if (ifNoneMatch) headers["If-None-Match"] = ifNoneMatch;

  const res = await fetch(`/api/runs/${runId}/extraction/entities`, {
    method:      "GET",
    credentials: "include",
    cache:       "no-store",
    headers,
  });

  if (res.status === 304) {
    return {notModified: true, payload: null};
  }

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = (await res.json()) as {detail?: unknown};
      if (typeof data.detail === "string") detail = data.detail;
    } catch {
      // ignore
    }
    throw new ApiError(res.status, detail);
  }

  const raw = (await res.json()) as Entity[] | RawEntitiesResponse;
  const etag = res.headers.get("etag");
  return {notModified: false, payload: normalizePayload(raw, etag)};
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

  const userId = useAuth((s) => s.user?.id);

  const [entities, setEntities] = useState<Entity[]>([]);
  const [meta, setMeta] = useState<Omit<EntitiesPayload, "entities">>({
    total: 0, approvedCount: 0, recordCount: 0, sourceCounts: {},
  });
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState<string | null>(null);

  const inFlight = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastFingerprint = useRef<string>("");
  const etagRef = useRef<string | null>(null);

  const applyPayload = useCallback((payload: EntitiesPayload, force = false): void => {
    const fp = entitiesFingerprint(payload.entities);
    if (
      !force
      && fp === lastFingerprint.current
      && entities.length === payload.entities.length
      && meta.total === payload.total
    ) {
      return;
    }
    lastFingerprint.current = fp;
    if (payload.etag) etagRef.current = payload.etag;
    setEntities(payload.entities);
    setMeta({
      total: payload.total,
      approvedCount: payload.approvedCount,
      recordCount: payload.recordCount,
      sourceCounts: payload.sourceCounts,
    });
  }, [entities.length, meta.total]);

  const fetchOnce = useCallback(async (force = false): Promise<void> => {
    if (!runId) return;
    if (inFlight.current) return;
    inFlight.current = true;
    setLoading(true);
    try {
      if (userId && !force) {
        const warm = getEntitiesCache(userId, runId, active);
        if (warm && warm.entities.length === warm.total) {
          applyPayload({
            entities:       warm.entities,
            total:          warm.total,
            approvedCount:  warm.approvedCount,
            recordCount:    warm.recordCount,
            sourceCounts:   warm.sourceCounts,
            etag:           warm.etag ?? null,
          }, false);
        }
      }

      const {notModified, payload} = await fetchEntitiesFromApi(
        runId, force ? null : etagRef.current,
      );

      if (notModified && userId) {
        const warm = getEntitiesCache(userId, runId, active);
        if (warm && warm.entities.length === warm.total) {
          applyPayload({
            entities:       warm.entities,
            total:          warm.total,
            approvedCount:  warm.approvedCount,
            recordCount:    warm.recordCount,
            sourceCounts:   warm.sourceCounts,
            etag:           warm.etag ?? etagRef.current,
          }, force);
          setError(null);
          return;
        }
      }

      if (payload) {
        applyPayload(payload, force);
        if (userId && payload.entities.length === payload.total) {
          setEntitiesCache(userId, runId, {
            entities:       payload.entities,
            total:          payload.total,
            approvedCount:  payload.approvedCount,
            recordCount:    payload.recordCount,
            sourceCounts:   payload.sourceCounts,
            etag:           payload.etag ?? null,
          });
        }
      }
      setError(null);
    } catch (e) {
      setError((e as Error).message || "Failed to load entities");
    } finally {
      setLoading(false);
      inFlight.current = false;
    }
  }, [runId, userId, active, applyPayload]);

  const refresh = useCallback(async (): Promise<void> => {
    lastFingerprint.current = "";
    etagRef.current = null;
    await fetchOnce(true);
  }, [fetchOnce]);

  useEffect(() => {
    lastFingerprint.current = "";
    etagRef.current = null;
    setEntities([]);
    setMeta({total: 0, approvedCount: 0, recordCount: 0, sourceCounts: {}});
  }, [runId, userId]);

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
      const detail = (ev as CustomEvent<{runId?: string; userId?: string}>).detail;
      if (detail?.runId && detail.runId !== runId) return;
      if (detail?.userId && userId && detail.userId !== userId) return;
      lastFingerprint.current = "";
      etagRef.current = null;
      void fetchOnce(true);
    }
    window.addEventListener(EXTRACTION_ENTITIES_REFRESH_EVENT, onRefreshed);
    return () => {
      window.removeEventListener(EXTRACTION_ENTITIES_REFRESH_EVENT, onRefreshed);
    };
  }, [runId, userId, fetchOnce]);

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
