/**
 * LRU memory + localStorage cache for liquid-glass displacement maps.
 *
 * `buildGlassMaps` is O(width × height) per surface; panels that remount or
 * resize by a few pixels were redoing the same work. Quantized dimensions
 * (8 px grid) improve hit rate without visible quality loss.
 */

import {
  buildGlassMaps,
  type GlassMapOptions,
  type GlassMaps,
} from "@/components/glass/liquidGlassMath";

const CACHE_VERSION = "v2";
const STORAGE_PREFIX = "mhm-glass:";
const INDEX_KEY = `${STORAGE_PREFIX}index`;
const MAX_MEMORY_ENTRIES = 48;
const MAX_STORAGE_BYTES = 3_500_000;
const MAX_ENTRY_BYTES = 120_000;
const QUANTIZE_STEP = 8;
const STORAGE_TTL_MS = 30 * 24 * 60 * 60 * 1000;

interface StoredGlassMaps extends GlassMaps {
  expiresAt?: number;
}

const memory = new Map<string, GlassMaps>();
const memoryOrder: string[] = [];

export function normalizeGlassMapOptions(opts: GlassMapOptions): GlassMapOptions {
  return {
    ...opts,
    width: Math.max(32, Math.round(opts.width / QUANTIZE_STEP) * QUANTIZE_STEP),
    height: Math.max(32, Math.round(opts.height / QUANTIZE_STEP) * QUANTIZE_STEP),
    radius: Math.round(opts.radius),
    bezelWidth: Math.round(opts.bezelWidth),
    thickness: Math.round(opts.thickness),
    ior: opts.ior ?? 1.5,
    surface: opts.surface ?? "convexSquircle",
  };
}

export function glassCacheKey(opts: GlassMapOptions): string {
  const n = normalizeGlassMapOptions(opts);
  return `${CACHE_VERSION}:${n.width}x${n.height}:r${n.radius}:b${n.bezelWidth}:t${n.thickness}:ior${n.ior}:s${n.surface}`;
}

function touchMemory(key: string): void {
  const idx = memoryOrder.indexOf(key);
  if (idx >= 0) memoryOrder.splice(idx, 1);
  memoryOrder.push(key);
  while (memoryOrder.length > MAX_MEMORY_ENTRIES) {
    const evict = memoryOrder.shift();
    if (evict) memory.delete(evict);
  }
}

function entryByteSize(maps: GlassMaps): number {
  return maps.displacementUrl.length + maps.specularUrl.length + 32;
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
    localStorage.setItem(INDEX_KEY, JSON.stringify(keys));
  } catch {
    // quota or private browsing
  }
}

function readStorage(key: string): GlassMaps | null {
  if (typeof localStorage === "undefined") return null;
  try {
    const raw = localStorage.getItem(`${STORAGE_PREFIX}${key}`);
    if (!raw) return null;
    const stored = JSON.parse(raw) as StoredGlassMaps;
    if (
      typeof stored?.displacementUrl !== "string"
      || typeof stored?.specularUrl !== "string"
      || typeof stored?.scale !== "number"
    ) {
      return null;
    }
    if (typeof stored.expiresAt === "number" && Date.now() > stored.expiresAt) {
      localStorage.removeItem(`${STORAGE_PREFIX}${key}`);
      return null;
    }
    const {expiresAt: _e, ...maps} = stored;
    return maps;
  } catch {
    return null;
  }
}

function writeStorage(key: string, maps: GlassMaps): void {
  if (typeof localStorage === "undefined") return;
  if (entryByteSize(maps) > MAX_ENTRY_BYTES) return;

  try {
    const payload = JSON.stringify({
      ...maps,
      expiresAt: Date.now() + STORAGE_TTL_MS,
    } satisfies StoredGlassMaps);
    let index = readIndex();
    let total = index.reduce((sum, k) => {
      const item = localStorage.getItem(`${STORAGE_PREFIX}${k}`);
      return sum + (item?.length ?? 0);
    }, 0);

    while (total + payload.length > MAX_STORAGE_BYTES && index.length > 0) {
      const old = index.shift();
      if (old) {
        const removed = localStorage.getItem(`${STORAGE_PREFIX}${old}`);
        total -= removed?.length ?? 0;
        localStorage.removeItem(`${STORAGE_PREFIX}${old}`);
      }
    }

    localStorage.setItem(`${STORAGE_PREFIX}${key}`, payload);
    if (!index.includes(key)) index.push(key);
    writeIndex(index);
  } catch {
    // quota exceeded
  }
}

function persistToStorage(key: string, maps: GlassMaps): void {
  const persist = () => writeStorage(key, maps);
  if (typeof requestIdleCallback !== "undefined") {
    requestIdleCallback(persist, {timeout: 2000});
  } else {
    setTimeout(persist, 0);
  }
}

/** Build or reuse cached displacement/specular maps for a glass surface. */
export function getGlassMaps(opts: GlassMapOptions): GlassMaps {
  const normalized = normalizeGlassMapOptions(opts);
  const key = glassCacheKey(normalized);

  const cached = memory.get(key);
  if (cached) {
    touchMemory(key);
    return cached;
  }

  const stored = readStorage(key);
  if (stored) {
    memory.set(key, stored);
    touchMemory(key);
    return stored;
  }

  const built = buildGlassMaps(normalized);
  memory.set(key, built);
  touchMemory(key);

  if (built.displacementUrl && built.specularUrl) {
    persistToStorage(key, built);
  }

  return built;
}

/** Test helper — clears in-memory cache only. */
export function clearGlassMapMemoryCache(): void {
  memory.clear();
  memoryOrder.length = 0;
}
