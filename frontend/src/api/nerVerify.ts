/**
 * NER AI verification client.
 *
 * Sibling of {@link "@/api/aiVerify"} that targets the new
 * ``/runs/{id}/extraction/ai-verify/...`` endpoint family. The
 * eval-agent runs over Stage-2 NER entities (person / provenance /
 * contents / genre) rather than Stage-3 authority matches; otherwise
 * the SSE event shapes, scope kinds, action registry, and session
 * replay surface are byte-identical to the authority flow.
 *
 * Body type carries ``entity_ids`` (not ``match_ids``) — that is the
 * only request-side delta. Server-side, the join key on returned
 * verdict candidates is ``_entity_id`` (not ``_match_id``).
 */

import { api } from "@/api/client";


export type ScopeKind = "single" | "selection" | "all";


export interface AgentActionMeta {
  id:             string;
  label:          string;
  description:    string;
  scope_kinds:    ScopeKind[];
  evaluators:     string[];
  min_candidates: number;
}


export interface AgentEvent {
  type: string;
  // The full payload as emitted by the eval-agent runner. Shape
  // varies per .type; consumers narrow downstream.
  [k: string]: unknown;
}


export interface VerifySessionListing {
  session_id: string;
  started_at: string | null;
  ended_at:   string | null;
  action_id:  string | null;
  scope_size: number;
  outcome:    string | null;
}


export interface VerifySession {
  session_id: string;
  run_id:     string;
  events:     AgentEvent[];
  verdicts:   Array<Record<string, unknown>>;
}


export interface StartRequest {
  action_id:   string;
  entity_ids?: string[];
  /** When true, skip the eval-agent verdict cache so Gemini is hit
   *  fresh on every candidate. Default false — repeated runs over the
   *  same scope serve from cache in milliseconds. */
  override_cache?: boolean;
  tier_model?: string;
}


/** Open the SSE stream for one NER verification session.
 *
 * Cancel by calling the returned cancel() — this aborts the fetch,
 * which propagates to uvicorn → the eval-agent subprocess gets a
 * SIGTERM. So "cancel" really stops Gemini calls.
 */
export function streamNerVerification(
  runId: string, req: StartRequest,
): {
  events: AsyncIterableIterator<AgentEvent>;
  cancel: () => void;
} {
  const controller = new AbortController();
  const events = (async function* (): AsyncIterableIterator<AgentEvent> {
    const res = await fetch(
      `/api/runs/${runId}/extraction/ai-verify/start-stream`,
      {
        method:      "POST",
        credentials: "include",
        headers:     { "Content-Type": "application/json" },
        body:        JSON.stringify(req),
        signal:      controller.signal,
      },
    );
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


function parseFrame(frame: string): AgentEvent | null {
  let type = "message";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith(": ")) continue;
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


export const NerVerify = {
  listActions: (runId: string, scopeKind: ScopeKind) =>
    api.get<AgentActionMeta[]>(
      `/runs/${runId}/extraction/ai-verify/actions?scope_kind=${scopeKind}`,
    ),
  listSessions: (runId: string) =>
    api.get<VerifySessionListing[]>(
      `/runs/${runId}/extraction/ai-verify/sessions`,
    ),
  session: (runId: string, sessionId: string) =>
    api.get<VerifySession>(
      `/runs/${runId}/extraction/ai-verify/sessions/${sessionId}`,
    ),
};
