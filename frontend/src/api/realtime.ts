/**
 * useProjectEvents(projectId, onEvent)
 *
 * Opens a WebSocket to /api/ws/projects/{id} and invokes `onEvent` for
 * every server-pushed message. Auto-reconnects with backoff. Closes on
 * unmount.
 */

import { useEffect, useRef } from "react";

export interface ProjectEventMessage {
  project_id: string;
  event_id: string;
  type: string;
  actor_id: string | null;
  created_at: string;
  payload: Record<string, unknown>;
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
