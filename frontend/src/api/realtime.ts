/**
 * useProjectEvents(projectId, onEvent)
 *
 * Opens a WebSocket to /api/ws/projects/{id} and invokes `onEvent` for
 * every server-pushed message. Auto-reconnects with backoff. Closes on
 * unmount.
 */

import { useEffect, useRef } from "react";

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

export function useProjectEvents(
  projectId: string | undefined,
  onEvent: (msg: ProjectEventMessage) => void,
) {
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    if (!projectId) return;

    let socket: WebSocket | null = null;
    let cancelled = false;
    let retry = 0;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    function connect() {
      if (cancelled) return;
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const url = `${proto}://${location.host}/api/ws/projects/${projectId}`;
      socket = new WebSocket(url);

      socket.addEventListener("message", (e) => {
        try {
          const msg = JSON.parse(e.data) as ProjectEventMessage;
          onEventRef.current(msg);
        } catch { /* ignore */ }
      });

      socket.addEventListener("open", () => {
        retry = 0;
      });

      socket.addEventListener("close", () => {
        if (cancelled) return;
        // Exponential backoff capped at 30s — typical Postgres restart
        // window during a Heroku platform upgrade.
        const delay = Math.min(30_000, 1_000 * 2 ** retry);
        retry += 1;
        retryTimer = setTimeout(connect, delay);
      });
    }

    connect();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      socket?.close();
    };
  }, [projectId]);
}
