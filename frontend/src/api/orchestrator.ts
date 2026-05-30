/**
 * Orchestrator client — wraps the SSE event stream from the FastAPI
 * /api/orchestrator/run-stream endpoint into an async iterator the UI
 * can consume directly.
 *
 * The browser's built-in EventSource doesn't support POST bodies, so
 * we use fetch + a ReadableStream reader and parse SSE frames by hand.
 * That's a few extra lines, but it lets us send the goal + budget on
 * the same request that opens the stream — no extra round-trip.
 */

import { api } from "@/api/client";


export interface OrchestratorEvent {
  type: string;
  // The full trace payload as emitted by eval-agent's TraceWriter.
  // Shape varies per event type — `data: Record<string, unknown>` is
  // the conservative typing; the consumer narrows per .type.
  [k: string]: unknown;
}


export interface RunRequest {
  goal:        string;
  mode?:       "plan_only" | "supervised" | "autonomous";
  judge_model?: string;
  max_steps?:  number;
  max_seconds?: number;
  max_usd?:    number;
  use_stub_judge?: boolean;
}


export interface SessionListing {
  session_id: string;
  started_at: string | null;
  ended_at:   string | null;
  outcome:    string | null;
  goal:       string | null;
  mode:       string | null;
  has_final:  boolean;
}


export interface SessionTrace {
  session_id: string;
  events:     OrchestratorEvent[];
  final_report_md: string;
}


/** Open the SSE stream and yield each event as it arrives.
 *
 * Cancel by calling the returned `cancel()` function — that aborts
 * the underlying fetch, which makes uvicorn signal asyncio.CancelledError
 * to the SSE producer, which in turn terminates the eval-agent
 * subprocess. So cancelling here is the user's "stop" button.
 */
export function streamOrchestrator(req: RunRequest): {
  events: AsyncIterableIterator<OrchestratorEvent>;
  cancel: () => void;
} {
  const controller = new AbortController();
  const events = (async function* (): AsyncIterableIterator<OrchestratorEvent> {
    const res = await fetch("/api/orchestrator/run-stream", {
      method:      "POST",
      credentials: "include",
      headers:     { "Content-Type": "application/json" },
      body:        JSON.stringify(req),
      signal:      controller.signal,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail ?? detail; } catch { /* ignore */ }
      throw new Error(detail);
    }
    if (!res.body) throw new Error("No SSE body");
    const reader  = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // SSE frame separator is a blank line. Split on \n\n.
      let sep = buf.indexOf("\n\n");
      while (sep >= 0) {
        const frame = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        const ev = parseFrame(frame);
        if (ev) yield ev;
        sep = buf.indexOf("\n\n");
      }
    }
  })();
  return {
    events,
    cancel: () => controller.abort(),
  };
}


function parseFrame(frame: string): OrchestratorEvent | null {
  // Each frame is multiple "field: value" lines.
  let type = "message";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith(": ")) continue;       // SSE comment (keepalive)
    const colon = line.indexOf(":");
    if (colon < 0) continue;
    const field = line.slice(0, colon);
    const value = line.slice(colon + 1).replace(/^ /, "");
    if      (field === "event") type = value;
    else if (field === "data")  data = value;
  }
  if (!data) return null;
  try {
    return { type, ...(JSON.parse(data) as Record<string, unknown>) };
  } catch {
    return { type, raw: data };
  }
}


export const Orchestrator = {
  listSessions: (limit = 20) =>
    api.get<SessionListing[]>(`/orchestrator/sessions?limit=${limit}`),
  trace: (sessionId: string) =>
    api.get<SessionTrace>(`/orchestrator/sessions/${sessionId}/trace`),
};
