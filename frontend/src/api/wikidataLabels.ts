/**
 * Lazy label resolver — turns bare P/Q ids into English labels by
 * batching the unknowns through /api/wikidata/labels (live Wikidata).
 *
 * Usage::
 *
 *   const {resolve, label} = useLabelStore();
 *   useEffect(() => { resolve(["Q5", "Q118384267", "P31"]); }, [...]);
 *   ...
 *   <span>{label("Q5") ?? "Q5"}</span>
 */

import {useCallback, useEffect, useMemo, useRef, useState} from "react";

import {api} from "@/api/client";


export function useLabelStore() {
  const [labels, setLabels] = useState<Record<string, string>>({});
  const labelsRef = useRef(labels);
  labelsRef.current = labels;
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
      setLabels((prev) => ({...prev, ...got}));
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
      if (labelsRef.current[id] !== undefined) continue;
      if (inFlightRef.current.has(id)) continue;
      if (pendingRef.current.has(id)) continue;
      pendingRef.current.add(id);
      added = true;
    }
    if (added && timerRef.current == null) {
      timerRef.current = setTimeout(flush, 60);
    }
  }, [flush]);

  const label = useCallback(
    (id: string | undefined | null): string | undefined =>
      id ? labels[id] : undefined,
    [labels],
  );

  const seed = useCallback((map: Record<string, string>) => {
    setLabels((prev) => {
      const additions: Record<string, string> = {};
      for (const [id, lbl] of Object.entries(map)) {
        if (lbl && prev[id] === undefined) {
          additions[id] = lbl;
        }
      }
      if (Object.keys(additions).length === 0) return prev;
      return {...prev, ...additions};
    });
  }, []);

  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current);
  }, []);

  return useMemo(
    () => ({labels, resolve, label, seed}),
    [labels, resolve, label, seed],
  );
}
