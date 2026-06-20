import {useEffect, useRef} from "react";

import type {Entity} from "@/api/extractionApprovals";
import {idsFingerprint, useLatestRef} from "@/utils/renderStable";

export function useReportDerivedIds(
  derivedIds: readonly string[],
  onChange?: (ids: string[]) => void,
): void {
  const onChangeRef = useLatestRef(onChange);
  const prevRef = useRef("");

  useEffect(() => {
    if (!onChangeRef.current) return;
    const fp = idsFingerprint(derivedIds);
    if (fp === prevRef.current) return;
    prevRef.current = fp;
    onChangeRef.current([...derivedIds]);
  }, [derivedIds, onChangeRef]);
}

/** Report a filtered entity list upstream only when member ids change. */
export function useReportFilteredEntities(
  filtered: Entity[],
  onChange?: (filtered: Entity[]) => void,
): void {
  const onChangeRef = useLatestRef(onChange);
  const prevRef = useRef("");
  const filteredRef = useRef(filtered);
  filteredRef.current = filtered;

  useEffect(() => {
    if (!onChangeRef.current) return;
    const fp = idsFingerprint(filtered.map((e) => e.id));
    if (fp === prevRef.current) return;
    prevRef.current = fp;
    onChangeRef.current(filteredRef.current);
  }, [filtered, onChangeRef]);
}
