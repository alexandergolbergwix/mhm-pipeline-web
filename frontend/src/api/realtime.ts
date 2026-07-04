/**
 * useProjectEvents(projectId, onEvent, options?)
 *
 * Opens a WebSocket to /api/ws/projects/{id} and invokes `onEvent` for
 * every server-pushed project event (debounced by default). Calls
 * `onReconnect` after a successful reconnect so callers can resync.
 * Auto-reconnects with backoff. Closes on unmount.
 */

import {useEffect, useRef} from "react";

import type { RunJobSnapshot } from "@/api/runJobs";

/**
 * The broker fans out two message shapes on the same channel, discriminated
 * by `type`: versioning events (`event_id`/`actor_id`/`created_at`/`payload`)
 * and `run_job_update` (`job`, a full RunJobSnapshot — see
 * run_job_service.py::_notify_job_update). Fields from the shape that
 * doesn't match `type` are simply absent, not wrong — check `type` first.
 */
export interface ProjectEventMessage {
  project_id: string;
  type: string;
  event_id?: string;
  actor_id?: string | null;
  created_at?: string;
  payload?: Record<string, unknown>;
  job?: RunJobSnapshot;
}

export interface UseProjectEventsOptions {
  /** Trailing-edge debounce for onEvent (ms). Default 400. Set 0 to disable. */
  debounceMs?: number;
  /** Invoked after a reconnect (not the initial connect) so pages can resync. */
  onReconnect?: () => void;
}

const DEFAULT_DEBOUNCE_MS = 400;

export function useProjectEvents(
  projectId: string | undefined,
  onEvent: (msg: ProjectEventMessage) => void,
  options?: UseProjectEventsOptions,
) {
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  const onReconnectRef = useRef(options?.onReconnect);
  onReconnectRef.current = options?.onReconnect;
  const debounceMs = options?.debounceMs ?? DEFAULT_DEBOUNCE_MS;

  useEffect(() => {
    if (!projectId) return;

    let socket: WebSocket | null = null;
    let cancelled = false;
    let retry = 0;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let debounceTimer: ReturnType<typeof setTimeout> | null = null;
    let hasConnected = false;

    function flushDebounced(msg: ProjectEventMessage) {
      if (debounceMs <= 0) {
        onEventRef.current(msg);
        return;
      }
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        debounceTimer = null;
        onEventRef.current(msg);
      }, debounceMs);
    }

    function connect() {
      if (cancelled) return;
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const url = `${proto}://${location.host}/api/ws/projects/${projectId}`;
      socket = new WebSocket(url);

      socket.addEventListener("message", (e) => {
        try {
          const msg = JSON.parse(e.data) as ProjectEventMessage;
          if (msg.type === "ping") return;
          flushDebounced(msg);
        } catch { /* ignore */ }
      });

      socket.addEventListener("open", () => {
        retry = 0;
        if (hasConnected) {
          onReconnectRef.current?.();
        } else {
          hasConnected = true;
        }
      });

      socket.addEventListener("close", () => {
        if (cancelled) return;
        const delay = Math.min(30_000, 1_000 * 2 ** retry);
        retry += 1;
        retryTimer = setTimeout(connect, delay);
      });
    }

    connect();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (debounceTimer) clearTimeout(debounceTimer);
      socket?.close();
    };
  }, [projectId, debounceMs]);
}
