/**
 * Lazy label resolver — turns bare P/Q ids into English labels by
 * batching the unknowns through /api/wikidata/labels (live Wikidata).
 *
 * Usage::
 *
 *   const { resolve, label } = useLabelStore();
 *   useEffect(() => { resolve(["Q5", "Q118384267", "P31"]); }, [...]);
 *   ...
 *   <span>{label("Q5") ?? "Q5"}</span>
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/api/client";


export function useLabelStore() {
  const [labels, setLabels] = useState<Record<string, string>>({});
  // pending = ids we've queued but haven't sent yet; inFlight prevents
  // duplicate requests for the same id while one is mid-flight.
  const pendingRef = useRef<Set<string>>(new Set());
  const inFlightRef = useRef<Set<string>>(new Set());
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flush = useCallback(async () => {
    timerRef.current = null;
    const ids = Array.from(pendingRef.current);
    pendingRef.current.clear();
    if (ids.length === 0) return;
    ids.forEach((i) => inFlightRef.current.add(i));
    try {
      const got = await api.get<Record<string, string>>(
        `/wikidata/labels?ids=${encodeURIComponent(ids.join(","))}`,
      );
      setLabels((prev) => ({ ...prev, ...got }));
    } catch { /* ignore — we'll just keep showing raw ids */ }
    finally {
      ids.forEach((i) => inFlightRef.current.delete(i));
    }
  }, []);

  const resolve = useCallback((rawIds: Array<string | undefined | null>) => {
    let added = false;
    for (const id of rawIds) {
      if (!id) continue;
      if (!/^[PQ]\d+$/.test(id)) continue;
      if (labels[id] !== undefined) continue;
      if (inFlightRef.current.has(id)) continue;
      if (pendingRef.current.has(id)) continue;
      pendingRef.current.add(id);
      added = true;
    }
    if (added && timerRef.current == null) {
      // Coalesce a burst of resolve() calls into one HTTP round-trip.
      timerRef.current = setTimeout(flush, 60);
    }
  }, [flush, labels]);

  const label = useCallback(
    (id: string | undefined | null): string | undefined =>
      id ? labels[id] : undefined,
    [labels],
  );

  /** Bulk-seed known labels from a server-provided map, skipping any ids
   *  already in the store (avoids overwriting fresher live-fetched data). */
  const seed = useCallback((map: Record<string, string>) => {
    setLabels((prev) => {
      const additions: Record<string, string> = {};
      for (const [id, lbl] of Object.entries(map)) {
        if (lbl && prev[id] === undefined) {
          additions[id] = lbl;
        }
      }
      if (Object.keys(additions).length === 0) return prev;
      return { ...prev, ...additions };
    });
  }, []);

  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current);
  }, []);

  return { labels, resolve, label, seed };
}
