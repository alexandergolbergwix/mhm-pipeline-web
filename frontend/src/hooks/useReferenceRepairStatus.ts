import {useCallback, useEffect, useRef, useState} from "react";

import {ApiError} from "@/api/client";
import {useProjectEvents} from "@/api/realtime";
import {ReferenceRepairs, type ReferenceRepairJob, type ReferenceRepairStatus} from "@/api/referenceRepairs";
import {useRunJobs} from "@/stores/runJobs";

export function useReferenceRepairStatus() {
  const [state, setState] = useState<ReferenceRepairStatus | null>(null);
  const [error, setError] = useState("");
  const [lastSync, setLastSync] = useState<number | null>(null);
  const generation = useRef(0);
  const refreshRef = useRef<() => void>(() => {});

  useEffect(() => {
    let stopped = false;
    let inflight = false;
    let dirty = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function refresh() {
      if (stopped) return;
      if (inflight) { dirty = true; return; }
      clearTimeout(timer);
      inflight = true;
      const request = ++generation.current;
      try {
        const status = await ReferenceRepairs.status();
        if (!stopped && request === generation.current) {
          setState(status);
          setLastSync(Date.now());
          setError("");
        }
      } catch (cause) {
        if (!stopped && request === generation.current) {
          if (cause instanceof ApiError && (cause.status === 401 || cause.status === 403)) setState(null);
          setError(cause instanceof Error ? cause.message : "The status refresh failed.");
        }
      } finally {
        inflight = false;
        if (!stopped) {
          const delay = dirty ? 0 : 2000;
          dirty = false;
          timer = setTimeout(() => void refresh(), delay);
        }
      }
    }
    refreshRef.current = () => void refresh();
    void refresh();
    return () => { stopped = true; ++generation.current; clearTimeout(timer); };
  }, []);

  const refresh = useCallback(() => refreshRef.current(), []);
  useProjectEvents(state?.project_id ?? state?.job?.project_id, message => {
    if (message.type === "run_job_update" && message.job?.kind === "wikidata_reference_repair"
      && message.job.run_id === state?.run_id) {
      useRunJobs.getState().upsertJob(message.job);
      refresh();
    }
  }, {onReconnect: refresh, debounceMs: 150});

  const acceptJob = useCallback((job: ReferenceRepairJob) => {
    ++generation.current;
    setState(previous => previous ? {...previous, job} : previous);
    refreshRef.current();
  }, []);

  return {state, error, lastSync, refresh, acceptJob};
}
