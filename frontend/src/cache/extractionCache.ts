/**
 * Browser localStorage tier for AI Extraction — user-scoped via clientCache.
 */

import type {Entity} from "@/api/extractionApprovals";
import type {AgentEvent} from "@/api/nerVerify";
import {
  getUserCache,
  invalidateRunForUser,
  setUserCache,
} from "@/cache/clientCache";
import {entityVerdictFingerprint} from "@/cache/verdictKey";

const NAMESPACE = "extraction";

const TTL_ENTITIES_IDLE_MS = 30_000;
const TTL_ENTITIES_ACTIVE_MS = 2_000;
const TTL_AI_VERDICT_MS = 7 * 24 * 60 * 60 * 1000;

export const EXTRACTION_ENTITIES_REFRESH_EVENT = "mhm.entities.refreshed";

interface EntitiesCacheValue {
  entities: Entity[];
  total: number;
  approvedCount: number;
  recordCount: number;
  sourceCounts: Record<string, number>;
  fetchedAt: number;
  etag?: string | null;
}

function entitiesValid(v: EntitiesCacheValue): boolean {
  return v.entities.length === v.total;
}

export function getEntitiesCache(
  userId: string,
  runId: string,
  active: boolean,
): EntitiesCacheValue | null {
  const hit = getUserCache<EntitiesCacheValue>(
    userId, NAMESPACE, "entities", runId, undefined,
    {validate: entitiesValid},
  );
  if (!hit) return null;
  const ttl = active ? TTL_ENTITIES_ACTIVE_MS : TTL_ENTITIES_IDLE_MS;
  if (Date.now() - hit.fetchedAt > ttl) return null;
  return hit;
}

export function setEntitiesCache(
  userId: string,
  runId: string,
  payload: Omit<EntitiesCacheValue, "fetchedAt">,
): void {
  setUserCache(
    userId, NAMESPACE, "entities", runId,
    {...payload, fetchedAt: Date.now()},
    {ttlMs: TTL_ENTITIES_IDLE_MS, fingerprint: payload.etag ?? undefined},
  );
}

export function invalidateRun(userId: string, runId: string): void {
  invalidateRunForUser(userId, runId);
  const legacy = `mhm:extraction:entities:${runId}`;
  if (typeof localStorage !== "undefined") {
    localStorage.removeItem(legacy);
    localStorage.removeItem(`mhm:extraction:ai_verdict:${runId}`);
  }
}

export async function invalidateEntityVerdict(
  userId: string,
  runId: string,
  ent: Entity,
): Promise<void> {
  try {
    const fp = await entityVerdictFingerprint(ent);
    const key = `mhm:${userId}:${NAMESPACE}:ai_verdict:${runId}:${fp}`;
    if (typeof localStorage !== "undefined") {
      localStorage.removeItem(key);
    }
  } catch {
    // fingerprint unavailable
  }
}

export function getAiVerdictCache(
  userId: string,
  runId: string,
  fingerprint: string,
): AgentEvent | null {
  return getUserCache<AgentEvent>(
    userId, NAMESPACE, "ai_verdict", runId, fingerprint,
  );
}

export function setAiVerdictCache(
  userId: string,
  runId: string,
  fingerprint: string,
  event: AgentEvent,
): void {
  setUserCache(
    userId, NAMESPACE, "ai_verdict", runId, event,
    {ttlMs: TTL_AI_VERDICT_MS, fingerprint},
  );
}

export function emitEntitiesRefreshed(userId: string, runId: string): void {
  invalidateRun(userId, runId);
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent(EXTRACTION_ENTITIES_REFRESH_EVENT, {detail: {runId, userId}}),
    );
  }
}
