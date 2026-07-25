import {useRef} from "react";

import type {RunJobSnapshot} from "@/api/runJobs";

/** Stable empty set for Zustand / filter fallbacks — never inline ``new Set()``. */
export const EMPTY_STRING_SET: Set<string> = new Set();
export const EMPTY_SET: ReadonlySet<string> = EMPTY_STRING_SET;

export function jobFingerprint(job: RunJobSnapshot): string {
  // Include result/error so a terminal snapshot (with outcomes) is distinct
  // from the last running progress tick — panels reload table state on succeed.
  const resultKey = job.result == null ? "0" : "1";
  return `${job.id}:${job.status}:${JSON.stringify(job.progress ?? {})}:${resultKey}:${job.error ?? ""}`;
}

export function idsFingerprint(ids: readonly string[]): string {
  return ids.join("\n");
}

export function useLatestRef<T>(value: T) {
  const ref = useRef(value);
  ref.current = value;
  return ref;
}
