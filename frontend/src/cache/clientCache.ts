/**
 * User-scoped browser cache (Tier 3).
 *
 * Keys: ``mhm:{userId}:{namespace}:{kind}[:{scopeId}][:fingerprint]``
 *
 * Global UI assets (glass maps) stay in their own module — never mix
 * user-private curator state with cross-user chrome.
 */

const GLOBAL_PREFIX = "mhm:global:";

export interface UserCacheOptions {
  ttlMs: number;
  fingerprint?: string;
}

export interface UserCacheGetOptions<T> {
  validate?: (value: T) => boolean;
}

interface CacheEnvelope<T> {
  value: T;
  expiresAt: number;
  fingerprint?: string;
}

function indexKey(userId: string): string {
  return `mhm:${userId}:__index__`;
}

function readIndex(userId: string): string[] {
  if (typeof localStorage === "undefined") return [];
  try {
    const raw = localStorage.getItem(indexKey(userId));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed)
      ? parsed.filter((k): k is string => typeof k === "string")
      : [];
  } catch {
    return [];
  }
}

function writeIndex(userId: string, keys: string[]): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(indexKey(userId), JSON.stringify(keys.slice(-400)));
  } catch {
    // quota
  }
}

function touchIndex(userId: string, key: string): void {
  const index = readIndex(userId).filter((k) => k !== key);
  index.push(key);
  writeIndex(userId, index);
}

export function userCacheKey(
  userId: string,
  namespace: string,
  kind: string,
  scopeId?: string,
  fingerprint?: string,
): string {
  const base = `mhm:${userId}:${namespace}:${kind}${scopeId ? `:${scopeId}` : ""}`;
  return fingerprint ? `${base}:${fingerprint}` : base;
}

function resolveKey(
  userId: string,
  namespace: string,
  kind: string,
  scopeId?: string,
  fingerprint?: string,
): string {
  return userCacheKey(userId, namespace, kind, scopeId, fingerprint);
}

export function getUserCache<T>(
  userId: string,
  namespace: string,
  kind: string,
  scopeId?: string,
  fingerprint?: string,
  opts?: UserCacheGetOptions<T>,
): T | null {
  if (!userId || typeof localStorage === "undefined") return null;
  const key = resolveKey(userId, namespace, kind, scopeId, fingerprint);
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const env = JSON.parse(raw) as CacheEnvelope<T>;
    if (!env || typeof env.expiresAt !== "number") return null;
    if (Date.now() > env.expiresAt) {
      localStorage.removeItem(key);
      return null;
    }
    if (fingerprint && env.fingerprint && env.fingerprint !== fingerprint) {
      return null;
    }
    if (opts?.validate && !opts.validate(env.value)) {
      return null;
    }
    return env.value;
  } catch {
    return null;
  }
}

export function setUserCache<T>(
  userId: string,
  namespace: string,
  kind: string,
  scopeId: string | undefined,
  value: T,
  opts: UserCacheOptions,
): void {
  if (!userId || typeof localStorage === "undefined") return;
  const key = resolveKey(userId, namespace, kind, scopeId, opts.fingerprint);
  try {
    const env: CacheEnvelope<T> = {
      value,
      expiresAt: Date.now() + opts.ttlMs,
      fingerprint: opts.fingerprint,
    };
    localStorage.setItem(key, JSON.stringify(env));
    touchIndex(userId, key);
  } catch {
    // quota
  }
}

export function clearUserCache(userId: string): void {
  if (!userId || typeof localStorage === "undefined") return;
  const prefix = `mhm:${userId}:`;
  for (const key of readIndex(userId)) {
    try {
      localStorage.removeItem(key);
    } catch {
      // ignore
    }
  }
  try {
    localStorage.removeItem(indexKey(userId));
  } catch {
    // ignore
  }
  for (const key of Object.keys(localStorage)) {
    if (key.startsWith(prefix)) {
      localStorage.removeItem(key);
    }
  }
}

export function invalidateRunForUser(userId: string, runId: string): void {
  if (!userId || typeof localStorage === "undefined") return;
  const needle = `:extraction:`;
  const runNeedle = `:${runId}`;
  for (const key of [...readIndex(userId), ...Object.keys(localStorage)]) {
    if (
      key.startsWith(`mhm:${userId}${needle}`)
      && key.includes(runNeedle)
    ) {
      try {
        localStorage.removeItem(key);
      } catch {
        // ignore
      }
    }
  }
  writeIndex(userId, readIndex(userId).filter(
    (k) => !(k.startsWith(`mhm:${userId}${needle}`) && k.includes(runNeedle)),
  ));
}

/** Prefix for global browser assets (glass maps, etc.). */
export function globalCacheKey(kind: string, id: string): string {
  return `${GLOBAL_PREFIX}${kind}:${id}`;
}
