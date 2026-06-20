import {useRef} from "react";

import type {RunJobSnapshot} from "@/api/runJobs";

export function jobFingerprint(job: RunJobSnapshot): string {
  return `${job.id}:${job.status}:${JSON.stringify(job.progress ?? {})}`;
}

export function idsFingerprint(ids: readonly string[]): string {
  return ids.join("\n");
}

export function useLatestRef<T>(value: T) {
  const ref = useRef(value);
  ref.current = value;
  return ref;
}
