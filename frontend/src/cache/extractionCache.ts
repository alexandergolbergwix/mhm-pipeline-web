/**
 * Browser localStorage tier for AI Extraction entity list + verdict warm reads.
 *
 * Keys: ``mhm:extraction:{kind}:{runId}[:{fingerprint}]``
 * TTL mirrors ``useApprovalStore`` poll intervals (30 s idle / 2 s active)
 * for entity lists; 7 d for per-input AI verdict snapshots.
 */

import type {Entity} from "@/api/extractionApprovals";
import type {AgentEvent} from "@/api/nerVerify";
import {entityVerdictFingerprint} from "@/cache/verdictKey";

const STORAGE_PREFIX = "mhm:extraction:";
const INDEX_KEY = `${STORAGE_PREFIX}index`;
const MAX_INDEX_ENTRIES = 200;

const TTL_ENTITIES_IDLE_MS = 30_000;
const TTL_ENTITIES_ACTIVE_MS = 2_000;
const TTL_AI_VERDICT_MS = 7 * 24 * 60 * 60 * 1000;

export const EXTRACTION_ENTITIES_REFRESH_EVENT = "mhm.entities.refreshed";

interface CacheEnvelope<T> {
  value: T;
  expiresAt: number;
}

interface EntitiesCacheValue {
  entities: Entity[];
  total: number;
  approvedCount: number;
  recordCount: number;
  sourceCounts: Record<string, number>;
  fetchedAt: number;
}

function storageKey(kind: string, runId: string, fingerprint?: string): string {
  const base = `${STORAGE_PREFIX}${kind}:${runId}`;
  return fingerprint ? `${base}:${fingerprint}` : base;
}

function readIndex(): string[] {
  if (typeof localStorage === "undefined") return [];
  try {
    const raw = localStorage.getItem(INDEX_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed) ? parsed.filter((k): k is string => typeof k === "string") : [];
  } catch {
    return [];
  }
}

function writeIndex(keys: string[]): void {
  if (typeof localStorage === "undefined") return;
  try {
    const trimmed = keys.slice(-MAX_INDEX_ENTRIES);
    localStorage.setItem(INDEX_KEY, JSON.stringify(trimmed));
  } catch {
    // quota or private browsing
  }
}

function touchIndex(key: string): void {
  const index = readIndex().filter((k) => k !== key);
  index.push(key);
  writeIndex(index);
}

function get<T>(key: string): T | null {
  if (typeof localStorage === "undefined") return null;
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const env = JSON.parse(raw) as CacheEnvelope<T>;
    if (!env || typeof env.expiresAt !== "number") return null;
    if (Date.now() > env.expiresAt) {
      localStorage.removeItem(key);
      return null;
    }
    return env.value;
  } catch {
    return null;
  }
}

function set<T>(key: string, value: T, ttlMs: number): void {
  if (typeof localStorage === "undefined") return;
  try {
    const env: CacheEnvelope<T> = {value, expiresAt: Date.now() + ttlMs};
    localStorage.setItem(key, JSON.stringify(env));
    touchIndex(key);
  } catch {
    // quota exceeded
  }
}

function remove(key: string): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.removeItem(key);
    writeIndex(readIndex().filter((k) => k !== key));
  } catch {
    // ignore
  }
}

export function getEntitiesCache(
  runId: string,
  active: boolean,
): EntitiesCacheValue | null {
  const key = storageKey("entities", runId);
  const ttl = active ? TTL_ENTITIES_ACTIVE_MS : TTL_ENTITIES_IDLE_MS;
  const hit = get<EntitiesCacheValue>(key);
  if (!hit) return null;
  if (Date.now() - hit.fetchedAt > ttl) return null;
  return hit;
}

export function setEntitiesCache(
  runId: string,
  payload: Omit<EntitiesCacheValue, "fetchedAt">,
): void {
  const key = storageKey("entities", runId);
  set(key, {...payload, fetchedAt: Date.now()}, TTL_ENTITIES_IDLE_MS);
}

export function invalidateRun(runId: string): void {
  remove(storageKey("entities", runId));
  const prefix = `${STORAGE_PREFIX}`;
  for (const key of readIndex()) {
    if (key.startsWith(`${prefix}entities:${runId}`)
      || key.startsWith(`${prefix}ai_verdict:${runId}`)) {
      remove(key);
    }
  }
}

export async function invalidateEntityVerdict(
  runId: string,
  ent: Entity,
): Promise<void> {
  try {
    const fp = await entityVerdictFingerprint(ent);
    remove(storageKey("ai_verdict", runId, fp));
  } catch {
    // fingerprint unavailable
  }
}

export function getAiVerdictCache(
  runId: string,
  fingerprint: string,
): AgentEvent | null {
  return get<AgentEvent>(storageKey("ai_verdict", runId, fingerprint));
}

export function setAiVerdictCache(
  runId: string,
  fingerprint: string,
  event: AgentEvent,
): void {
  set(storageKey("ai_verdict", runId, fingerprint), event, TTL_AI_VERDICT_MS);
}

export function emitEntitiesRefreshed(runId: string): void {
  invalidateRun(runId);
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent(EXTRACTION_ENTITIES_REFRESH_EVENT, {detail: {runId}}),
    );
  }
}
